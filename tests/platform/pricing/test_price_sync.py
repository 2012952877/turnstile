"""What the sync decides, and what it refuses to decide.

A wrong rate here is silent -- the dashboard renders, the bill adds up, and nobody notices for
weeks. So most of these cases are about *not* writing.

The last two are contract tests: the in-memory repository stands in for PostgreSQL in every
other test in this suite, so wherever the two disagree, the suite agrees with the fake.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from turnstile_core.domain.runtime_models import ManagedModel, PriceSource, PriceSyncStatus
from turnstile_core.persistence.in_memory_registry import InMemoryRegistryRepositoryMixin
from turnstile_core.pricing.catalog import CatalogEntry, CompositeCatalog
from turnstile_core.pricing.sync import plan_price_sync

REFERENCE = "azure_retail:Azure OpenAI:gpt 4.1 mini:Global:*"


class StubCatalog(CompositeCatalog):
    """Answers one reference, and can be told to fail the way a network source fails."""

    def __init__(self, entry: CatalogEntry | None, *, raises: bool = False) -> None:
        self._entry = entry
        self._raises = raises
        self.lookups: list[str] = []

    def lookup(self, reference: str) -> CatalogEntry | None:
        self.lookups.append(reference)
        if self._raises:
            raise TimeoutError("price API unreachable")
        return self._entry if reference == REFERENCE else None


def entry(
    input_per_million: float = 0.40,
    output_per_million: float = 1.60,
    cached_per_million: float | None = 0.10,
) -> CatalogEntry:
    return CatalogEntry(
        reference=REFERENCE,
        label="gpt 4.1 mini",
        source=PriceSource.AZURE_RETAIL,
        input_per_million=input_per_million,
        output_per_million=output_per_million,
        cached_per_million=cached_per_million,
        cache_write_per_million=None,
    )


def model(**overrides: Any) -> ManagedModel:
    base: dict[str, Any] = {
        "id": uuid4(),
        "provider_id": uuid4(),
        "runtime_id": uuid4(),
        "model_key": "gpt-4.1-mini",
        "display_name": "gpt 4.1 mini",
        "provider_name": "Foundry",
        "runtime_name": "connection",
        "family_key": "generic",
        "upstream_model_id": "gpt-4.1-mini",
        "price_source": PriceSource.AZURE_RETAIL,
        "price_reference": REFERENCE,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    return ManagedModel.model_validate(base | overrides)


def only(summary: Any) -> Any:
    assert len(summary.updates) == 1, summary.updates
    return summary.updates[0]


# ------------------------------------------------------------------------------------------
# The arithmetic
# ------------------------------------------------------------------------------------------


def test_the_charged_rate_is_the_list_price_times_the_discount() -> None:
    update = only(plan_price_sync([model(effective_discount_percent=90)], StubCatalog(entry())))
    assert update.status is PriceSyncStatus.OK
    assert update.writes is True
    assert update.list_input_cost_per_million == pytest.approx(0.40)
    assert update.input_cost_per_million == pytest.approx(0.36)
    assert update.output_cost_per_million == pytest.approx(1.44)
    assert update.cached_cost_per_million == pytest.approx(0.09)


def test_no_discount_charges_the_list_price() -> None:
    update = only(plan_price_sync([model()], StubCatalog(entry())))
    assert update.input_cost_per_million == pytest.approx(0.40)


def test_a_bucket_the_vendor_does_not_publish_stays_empty() -> None:
    """Not zero. Zero is a price, and it would quietly stop charging for cache writes."""
    update = only(
        plan_price_sync(
            [model(effective_discount_percent=66)],
            StubCatalog(entry(cached_per_million=None)),
        )
    )
    assert update.cached_cost_per_million is None
    assert update.cache_write_cost_per_million is None


# ------------------------------------------------------------------------------------------
# The refusals
# ------------------------------------------------------------------------------------------


def test_a_model_priced_by_hand_is_not_touched_at_all() -> None:
    """The contract that lets this ship without disturbing anyone's existing rates."""
    catalog = StubCatalog(entry())
    summary = plan_price_sync(
        [model(price_source=PriceSource.MANUAL, price_reference=None)], catalog
    )
    assert summary.updates == ()
    assert catalog.lookups == [], "a manual model should not even be looked up"


def test_a_reference_that_resolves_to_nothing_keeps_the_current_rates() -> None:
    update = only(plan_price_sync([model(price_reference="azure_retail:gone:x:Global:*")],
                                  StubCatalog(entry())))
    assert update.status is PriceSyncStatus.UNMAPPED
    assert update.writes is False
    assert update.input_cost_per_million is None


def test_a_source_that_cannot_be_read_keeps_the_current_rates() -> None:
    update = only(plan_price_sync([model()], StubCatalog(entry(), raises=True)))
    assert update.status is PriceSyncStatus.STALE
    assert update.writes is False


def test_an_entry_missing_half_its_price_is_not_used() -> None:
    update = only(plan_price_sync([model()], StubCatalog(entry(output_per_million=None))))  # type: ignore[arg-type]
    assert update.status is PriceSyncStatus.UNMAPPED
    assert update.writes is False


def test_a_list_price_that_jumped_waits_for_a_person() -> None:
    """A parser reading the wrong column looks exactly like a repricing. Stop for both."""
    update = only(
        plan_price_sync(
            [model(list_input_cost_per_million=0.40, effective_discount_percent=90)],
            StubCatalog(entry(input_per_million=4.0)),
        )
    )
    assert update.status is PriceSyncStatus.REVIEW_NEEDED
    assert update.writes is False
    assert update.list_input_cost_per_million == pytest.approx(4.0), (
        "the new list price is still reported, so a person can see what it wants to become"
    )
    assert update.input_cost_per_million is None


def test_an_ordinary_repricing_passes_unattended() -> None:
    update = only(
        plan_price_sync(
            [model(list_input_cost_per_million=0.40, effective_discount_percent=90)],
            StubCatalog(entry(input_per_million=0.44)),
        )
    )
    assert update.status is PriceSyncStatus.OK
    assert update.input_cost_per_million == pytest.approx(0.396)


def test_drift_is_measured_against_the_list_price_not_the_charged_rate() -> None:
    """Otherwise changing a discount would read as a suspicious price move and block itself."""
    update = only(
        plan_price_sync(
            [model(list_input_cost_per_million=0.40, input_cost_per_million=0.36,
                   effective_discount_percent=20)],
            StubCatalog(entry(input_per_million=0.40)),
        )
    )
    assert update.status is PriceSyncStatus.OK
    assert update.input_cost_per_million == pytest.approx(0.08)


def test_only_narrows_the_run_to_the_models_asked_for() -> None:
    wanted, other = model(), model()
    summary = plan_price_sync([wanted, other], StubCatalog(entry()), only=[wanted.id])
    assert [update.model_id for update in summary.updates] == [wanted.id]


# ------------------------------------------------------------------------------------------
# Repository contract: the fake has to answer the same question as the database
# ------------------------------------------------------------------------------------------


class Registry(InMemoryRegistryRepositoryMixin):
    def __init__(self, runtimes: list[dict[str, Any]], models: list[dict[str, Any]]) -> None:
        self.gateways = []
        self.providers = []
        self.runtimes = runtimes
        self.models = models
        self.gateway_publications = []
        self.effective_gateway_releases = {}


def registry_with(model_discount: float | None, runtime_discount: float | None) -> Registry:
    runtime_id = uuid4()
    return Registry(
        runtimes=[{"id": runtime_id, "provider_id": uuid4(),
                   "price_discount_percent": runtime_discount}],
        models=[{"id": uuid4(), "runtime_id": runtime_id, "model_key": "m",
                 "price_discount_percent": model_discount}],
    )


@pytest.mark.parametrize(
    ("model_discount", "runtime_discount", "expected"),
    [
        (None, 90, 90),   # inherited from the connection -- the usual case
        (66, 90, 66),     # the model overrides its connection
        (None, None, None),  # nobody set one: list price
        (66, None, 66),
    ],
)
def test_the_fake_resolves_the_discount_the_way_the_query_does(
    model_discount: float | None, runtime_discount: float | None, expected: float | None
) -> None:
    registry = registry_with(model_discount, runtime_discount)
    assert registry.registry()["models"][0]["effective_discount_percent"] == expected


def test_a_sync_result_lands_on_the_model_the_fake_reports() -> None:
    registry = registry_with(None, 90)
    model_id: UUID = registry.models[0]["id"]
    written = registry.apply_model_price_sync([
        {"model_id": model_id, "writes": True, "status": "ok", "message": None,
         "input_cost_per_million": 0.36, "output_cost_per_million": 1.44,
         "cached_cost_per_million": 0.09, "cache_write_cost_per_million": None,
         "list_input_cost_per_million": 0.40, "list_output_cost_per_million": 1.60,
         "list_cached_cost_per_million": 0.10, "list_cache_write_cost_per_million": None},
    ])
    assert written == 1
    stored = registry.registry()["models"][0]
    assert stored["input_cost_per_million"] == pytest.approx(0.36)
    assert stored["list_input_cost_per_million"] == pytest.approx(0.40)
    assert stored["price_sync_status"] == "ok"


def test_a_skipped_sync_records_the_reason_without_moving_the_rates() -> None:
    registry = registry_with(None, 90)
    registry.models[0]["input_cost_per_million"] = 1.2345
    registry.apply_model_price_sync([
        {"model_id": registry.models[0]["id"], "writes": False,
         "status": "unmapped", "message": "找不到",
         "list_input_cost_per_million": None, "list_output_cost_per_million": None,
         "list_cached_cost_per_million": None, "list_cache_write_cost_per_million": None},
    ])
    stored = registry.registry()["models"][0]
    assert stored["input_cost_per_million"] == pytest.approx(1.2345)
    assert stored["price_sync_status"] == "unmapped"
    assert stored["price_sync_message"] == "找不到"
