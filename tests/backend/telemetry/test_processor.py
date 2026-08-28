from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from backend.domain.application_access import (
    GatewayApplicationDiscovery,
    GatewayApplicationDiscoveryItem,
)
from backend.domain.models import ModelIdentity, ModelPrice, TokenUsageRecord
from backend.ingestion.processor import (
    CoefficientResolver,
    UsageProcessor,
    calculate_et,
)
from backend.persistence.in_memory import InMemoryRepository
from backend.services.application_access import ApplicationAccessService

GATEWAY_ID = UUID("10000000-0000-4000-8000-000000000001")


class RecordingRepository:
    def __init__(
        self,
        prices: dict[str, ModelPrice] | None = None,
        identities: dict[str, ModelIdentity] | None = None,
    ) -> None:
        self.records: list[TokenUsageRecord] = []
        self.prices = prices or {}
        self.identities = identities or {}
        self.price_reads = 0
        self.identity_reads = 0

    def write_token_usage(
        self, record: TokenUsageRecord, application: object | None = None
    ) -> None:
        assert application is None
        self.records.append(record)

    def model_prices(self) -> dict[str, ModelPrice]:
        self.price_reads += 1
        return self.prices

    def model_identities(self) -> dict[str, ModelIdentity]:
        self.identity_reads += 1
        return self.identities

    def gateway_application_attribution_map(
        self, gateway_profile_id: UUID
    ) -> list[dict[str, object]]:
        del gateway_profile_id
        return []


def event(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "usage-1",
        "ts": datetime(2026, 7, 17, tzinfo=UTC).isoformat(),
        "team": "platform",
        "user": "lei",
        "agent": "delivery-engineer",
        "workflow": "pull-request-review",
        "run_id": "run-1",
        "turn_index": 1,
        "provider": "aoai",
        "model": "gpt-4.1-mini",
        "input_tokens": 1000,
        "cached_tokens": 200,
        "output_tokens": 100,
        "latency_ms": 1200,
        "status": "success",
        "ingest_source": "eventhub",
    }
    value.update(overrides)
    return value


def test_calculate_et() -> None:
    assert calculate_et(1000, 200, 100, 0.5) == 710


def test_coefficient_snapshot_is_written() -> None:
    repository = RecordingRepository()
    processor = UsageProcessor(repository, CoefficientResolver({"aoai/gpt-4.1-mini": 0.35}))
    assert processor.process(event()) is True
    assert repository.records[0].et_coeff_m == 0.35
    assert repository.records[0].et == 497
    assert repository.records[0].request_id == "usage-1"
    assert repository.records[0].correlation_id == "usage-1"
    assert repository.records[0].status_code == 200


def test_enterprise_attribution_and_failure_fields_are_preserved() -> None:
    repository = RecordingRepository()
    processor = UsageProcessor(repository, CoefficientResolver({"default": 1.0}))
    raw = event(
        request_id="request-42",
        correlation_id="apim-42",
        organization="Contoso",
        organization_id="org-1",
        department="Platform",
        department_id="department-1",
        project="FinOps",
        project_id="project-1",
        agent_id="agent-1",
        user_id="user-1",
        model_id="deployment-1",
        request_source="agent-console",
        status="429",
        status_code=429,
        estimated_cost=0.0123,
        error_message="rate limited",
    )
    assert processor.process(raw) is True
    record = repository.records[0]
    assert record.organization_id == "org-1"
    assert record.department_id == "department-1"
    assert record.project_id == "project-1"
    assert record.agent_id == "agent-1"
    assert record.user_id == "user-1"
    assert record.model_id == "deployment-1"
    assert record.request_source == "agent-console"
    assert record.usage_domain == "apim"
    assert record.status_code == 429
    assert record.estimated_cost == 0.0123
    assert record.error_message == "rate limited"


def test_apim_subscription_is_resolved_to_immutable_application_attribution() -> None:
    repository = InMemoryRepository()
    ApplicationAccessService(repository, sync_available=True).sync_discovery(
        GatewayApplicationDiscovery(
            gateway_profile_id=GATEWAY_ID,
            discovered_at=datetime(2026, 8, 26, tzinfo=UTC),
            items=[
                GatewayApplicationDiscoveryItem(
                    apim_subscription_id="outline-assistant",
                    display_name="Outline Assistant",
                    state="active",
                    scope_type="product",
                    scope_id="finops-applications",
                    scope_exists=True,
                    application_type="service",
                    system_managed=False,
                )
            ],
        ),
        "application-sync-worker",
    )
    processor = UsageProcessor(repository, CoefficientResolver({"default": 1.0}))

    assert processor.process(
        event(
            gateway_profile_id=str(GATEWAY_ID),
            apim_subscription_id="outline-assistant",
            application_actor_type="service",
            application_actor_id="service:outline-assistant",
            application_admission="ok",
        )
    )

    attribution = repository.usage_application_attributions["usage-1"]
    assert attribution.application_name_snapshot == "Outline Assistant"
    assert attribution.actor_type == "service"
    assert attribution.actor_id == "service:outline-assistant"
    assert attribution.application_admission == "ok"


def test_copilot_assistant_events_are_classified_outside_the_apim_domain() -> None:
    repository = RecordingRepository()
    processor = UsageProcessor(repository, CoefficientResolver({"default": 1.0}))

    assert processor.process(event(request_source="copilot-assistant")) is True
    assert repository.records[0].usage_domain == "github_copilot"


def test_stream_without_breakdown_is_persisted_without_inventing_a_split() -> None:
    repository = RecordingRepository()
    processor = UsageProcessor(repository, CoefficientResolver({"default": 1.0}))
    raw = event(input_tokens=None, cached_tokens=None, output_tokens=None, tokens_consumed=1000)
    assert processor.process(raw) is True
    record = repository.records[0]
    assert record.input_tokens == 0
    assert record.cached_tokens == 0
    assert record.output_tokens == 0
    assert record.estimated is True
    assert record.ingest_error == "stream_usage_breakdown_unavailable"


def test_exact_adapter_event_upgrades_only_the_estimated_placeholder() -> None:
    repository = InMemoryRepository()
    processor = UsageProcessor(repository, CoefficientResolver({"default": 1.0}))

    assert processor.process(
        event(
            id="shared-request",
            input_tokens=None,
            cached_tokens=None,
            output_tokens=None,
            tokens_consumed=1000,
            budget_admission="ok",
            model_admission="ok",
        )
    )
    assert processor.process(
        event(
            id="shared-request",
            input_tokens=72,
            cached_tokens=1128,
            cache_write_tokens=128,
            output_tokens=30,
            estimated=False,
            ingest_source="eventhub",
        )
    )
    assert processor.process(
        event(
            id="shared-request",
            input_tokens=999,
            cached_tokens=0,
            output_tokens=999,
            estimated=False,
        )
    )

    matching = [record for record in repository.usage_records if record.id == "shared-request"]
    assert len(matching) == 1
    assert matching[0].input_tokens == 72
    assert matching[0].cached_tokens == 1128
    assert matching[0].cache_write_tokens == 128
    assert matching[0].output_tokens == 30
    assert matching[0].estimated is False
    assert matching[0].budget_admission == "ok"
    assert matching[0].model_admission == "ok"


def test_later_apim_exact_event_adds_admission_without_replacing_observer_usage() -> None:
    repository = InMemoryRepository()
    processor = UsageProcessor(repository, CoefficientResolver({"default": 1.0}))

    assert processor.process(
        event(
            id="exact-first",
            input_tokens=72,
            cached_tokens=1128,
            cache_write_tokens=128,
            output_tokens=30,
            estimated=False,
            ingest_source="eventhub",
        )
    )
    assert processor.process(
        event(
            id="exact-first",
            input_tokens=999,
            cached_tokens=0,
            output_tokens=999,
            estimated=False,
            budget_admission="ok",
            model_admission="ok",
        )
    )

    matching = [record for record in repository.usage_records if record.id == "exact-first"]
    assert len(matching) == 1
    assert matching[0].input_tokens == 72
    assert matching[0].cached_tokens == 1128
    assert matching[0].cache_write_tokens == 128
    assert matching[0].output_tokens == 30
    assert matching[0].budget_admission == "ok"
    assert matching[0].model_admission == "ok"


def test_postgres_upsert_preserves_exact_observer_usage_when_admission_arrives() -> None:
    source = (
        Path(__file__).resolve().parents[3] / "backend/persistence/repository.py"
    ).read_text(encoding="utf-8")

    assert "input_tokens = CASE WHEN existing.estimated" in source
    assert "THEN EXCLUDED.input_tokens ELSE existing.input_tokens END" in source
    assert "runtime = CASE WHEN existing.estimated" in source
    assert "THEN EXCLUDED.runtime ELSE existing.runtime END" in source
    assert "existing.budget_admission, EXCLUDED.budget_admission" in source
    assert "existing.model_admission, EXCLUDED.model_admission" in source


def test_malformed_event_does_not_raise_or_write() -> None:
    repository = RecordingRepository()
    processor = UsageProcessor(repository, CoefficientResolver({"default": 1.0}))
    assert processor.process({"id": "broken"}) is False
    assert repository.records == []


def test_missing_usage_is_persisted_and_flagged() -> None:
    repository = RecordingRepository()
    processor = UsageProcessor(repository, CoefficientResolver({"default": 1.0}))
    assert (
        processor.process(event(input_tokens=None, cached_tokens=None, output_tokens=None)) is True
    )
    record = repository.records[0]
    assert record.input_tokens == 0
    assert record.cached_tokens == 0
    assert record.output_tokens == 0
    assert record.estimated is True
    assert record.ingest_error == "usage_unavailable"


def sonnet_prices() -> dict[str, ModelPrice]:
    return {
        "gpt-4.1-mini": ModelPrice(
            input_price_per_million=3.0,
            cached_price_per_million=0.3,
            cache_write_price_per_million=3.75,
            output_price_per_million=15.0,
        )
    }


def test_cost_is_priced_at_ingestion_with_the_cached_rate() -> None:
    repository = RecordingRepository(sonnet_prices())
    processor = UsageProcessor(repository, CoefficientResolver({"default": 1.0}))

    assert processor.process(event()) is True

    record = repository.records[0]
    # 1000 input at 3.00, 200 cached at 0.30, 100 output at 15.00.
    assert record.estimated_cost == round((1000 * 3.0 + 200 * 0.3 + 100 * 15.0) / 1_000_000, 8)
    assert record.input_price_per_million == 3.0
    assert record.cached_price_per_million == 0.3
    assert record.output_price_per_million == 15.0


def test_cache_writes_are_priced_above_cache_reads() -> None:
    repository = RecordingRepository(sonnet_prices())
    processor = UsageProcessor(repository, CoefficientResolver({"default": 1.0}))

    # 200 cached tokens of which 150 were written, leaving 50 reads.
    assert processor.process(event(cache_write_tokens=150)) is True

    record = repository.records[0]
    assert record.cache_write_tokens == 150
    assert record.cache_write_price_per_million == 3.75
    assert record.estimated_cost == round(
        (1000 * 3.0 + 50 * 0.3 + 150 * 3.75 + 100 * 15.0) / 1_000_000, 8
    )


def test_an_unreported_split_prices_every_cached_token_as_a_read() -> None:
    repository = RecordingRepository(sonnet_prices())
    processor = UsageProcessor(repository, CoefficientResolver({"default": 1.0}))

    assert processor.process(event()) is True

    record = repository.records[0]
    assert record.cache_write_tokens == 0
    assert record.estimated_cost == round((1000 * 3.0 + 200 * 0.3 + 100 * 15.0) / 1_000_000, 8)


def test_a_write_count_above_the_cache_bucket_is_clamped() -> None:
    repository = RecordingRepository(sonnet_prices())
    processor = UsageProcessor(repository, CoefficientResolver({"default": 1.0}))

    # The database constrains writes to the cache bucket, so a malformed event must not exceed it.
    assert processor.process(event(cache_write_tokens=9999)) is True

    record = repository.records[0]
    assert record.cache_write_tokens == 200
    assert record.estimated_cost == round((1000 * 3.0 + 200 * 3.75 + 100 * 15.0) / 1_000_000, 8)


def test_registry_id_takes_priority_over_the_model_key() -> None:
    prices = sonnet_prices()
    prices["deployment-1"] = ModelPrice(
        input_price_per_million=1.0,
        cached_price_per_million=0.5,
        cache_write_price_per_million=1.25,
        output_price_per_million=6.0,
    )
    repository = RecordingRepository(prices)
    processor = UsageProcessor(repository, CoefficientResolver({"default": 1.0}))

    assert processor.process(event(model_id="deployment-1")) is True

    assert repository.records[0].input_price_per_million == 1.0


def test_unpriced_model_records_no_cost_and_no_prices() -> None:
    repository = RecordingRepository()
    processor = UsageProcessor(repository, CoefficientResolver({"default": 1.0}))

    assert processor.process(event()) is True

    record = repository.records[0]
    assert record.estimated_cost == 0
    assert record.input_price_per_million is None
    assert record.cached_price_per_million is None
    assert record.output_price_per_million is None


def test_registry_is_read_once_per_batch_not_per_event() -> None:
    repository = RecordingRepository(sonnet_prices())
    processor = UsageProcessor(repository, CoefficientResolver({"default": 1.0}))

    for index in range(5):
        assert processor.process(event(id=f"usage-{index}")) is True

    assert repository.price_reads == 1
    assert repository.identity_reads == 1


def test_registry_uuid_and_model_key_collapse_onto_one_identity() -> None:
    identity = ModelIdentity(model_id="307940d3", display_name="Claude Sonnet 5")
    identities = {"307940d3": identity, "databricks-claude-sonnet-5": identity}
    repository = RecordingRepository(identities=identities)
    processor = UsageProcessor(repository, CoefficientResolver({"default": 1.0}))

    # The dashboard BFF sends the registry UUID; an employee desktop client sends the model key.
    assert processor.process(event(id="bff", model_id="307940d3", model="gpt-name-drift")) is True
    assert processor.process(
        event(
            id="desktop",
            model_id="databricks-claude-sonnet-5",
            model="databricks-claude-sonnet-5",
        )
    ) is True

    assert [(item.model_id, item.model) for item in repository.records] == [
        ("307940d3", "Claude Sonnet 5"),
        ("307940d3", "Claude Sonnet 5"),
    ]


def test_a_model_missing_from_the_registry_keeps_the_caller_identifiers() -> None:
    repository = RecordingRepository()
    processor = UsageProcessor(repository, CoefficientResolver({"default": 1.0}))

    assert processor.process(event(model_id="unknown-1", model="mystery-model")) is True

    record = repository.records[0]
    assert record.model_id == "unknown-1"
    assert record.model == "mystery-model"


def test_a_failing_registry_read_records_usage_without_a_cost() -> None:
    class FailingRepository(RecordingRepository):
        def model_prices(self) -> dict[str, ModelPrice]:
            raise RuntimeError("registry unavailable")

    repository = FailingRepository()
    processor = UsageProcessor(repository, CoefficientResolver({"default": 1.0}))

    assert processor.process(event()) is True

    record = repository.records[0]
    assert record.input_tokens == 1000
    assert record.estimated_cost == 0
    assert record.input_price_per_million is None


def test_failed_request_without_usage_is_persisted() -> None:
    repository = RecordingRepository()
    processor = UsageProcessor(repository, CoefficientResolver({"default": 1.0}))
    assert processor.process(
        event(
            input_tokens=None,
            cached_tokens=None,
            output_tokens=None,
            status="502",
            status_code=502,
            error_message="upstream unavailable",
        )
    ) is True
    record = repository.records[0]
    assert record.total_tokens if hasattr(record, "total_tokens") else 0 == 0
    assert record.status_code == 502
    assert record.ingest_error == "failed_request_has_no_usage"
