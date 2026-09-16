"""The catalogue's two jobs: read a vendor's meter names, and find the one a person means.

Both are exercised against meter names copied verbatim from the Azure retail price API, because
the naming is not consistent between model families and every inconsistency is a chance to drop
a model's price without anyone noticing.
"""

from __future__ import annotations

from typing import Any

import pytest

from turnstile_core.domain.runtime_models import PriceSource
from turnstile_core.pricing.catalog import (
    AzureRetailCatalog,
    CatalogEntry,
    CompositeCatalog,
)


def meter(name: str, price: float, region: str = "koreacentral") -> dict[str, Any]:
    return {
        "meterName": name,
        "retailPrice": price,
        "unitOfMeasure": "1K",
        "armRegionName": region,
    }


class StubAzure(AzureRetailCatalog):
    """Same parsing, no network."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__()
        self._rows = rows
        self.last_filter = ""

    def _fetch(self, filter_expression: str) -> list[dict[str, Any]]:
        self.last_filter = filter_expression
        return self._rows


GPT_41 = [
    meter("gpt 4.1 Inp glbl Tokens", 0.002),
    meter("gpt 4.1 Outp glbl Tokens", 0.008),
    meter("gpt 4.1 cached Inp glbl Tokens", 0.0005),
]
# gpt-5 spells the buckets differently, which is the whole point of this fixture.
GPT_5_PRO = [
    meter("gpt 5 pro inp glbl Tokens", 0.015),
    meter("gpt 5 pro out glbl Tokens", 0.12),
]
GPT_5_CODEX = [
    meter("gpt-5-codex-inp-glbl Tokens", 0.00125),
    meter("gpt-5-codex-out-glbl Tokens", 0.01),
    meter("gpt-5-codex-ccchd-inp-glbl Tokens", 0.000125),
]
NOISE = [
    meter("gpt 4o 0513 Input global Tokens", 0.005),
    meter("gpt 4o 0513 Output global Tokens", 0.015),
    meter("gpt 5 pro batch inp glbl Tokens", 0.0075),
    meter("gpt-4.1-nano FT Training global Tokens", 0.0015),
]


def entries(rows: list[dict[str, Any]]) -> dict[str, CatalogEntry]:
    catalog = StubAzure(rows)
    return {entry.label: entry for entry in catalog.entries(region="koreacentral")}


def test_reads_the_bucket_names_every_model_family_uses() -> None:
    found = entries(GPT_41 + GPT_5_PRO + GPT_5_CODEX)
    assert set(found) == {"gpt 4.1", "gpt 5 pro", "gpt 5 codex"}
    assert found["gpt 4.1"].input_per_million == pytest.approx(2.0)
    assert found["gpt 4.1"].output_per_million == pytest.approx(8.0)
    assert found["gpt 4.1"].cached_per_million == pytest.approx(0.5)
    # `out` rather than `Outp`; without it this entry has no output rate and is dropped as
    # unpriced, which is how searching for gpt-5 came back with gpt-4o.
    assert found["gpt 5 pro"].output_per_million == pytest.approx(120.0)
    assert found["gpt 5 pro"].priced
    # Hyphen-separated, and `ccchd` for cached.
    assert found["gpt 5 codex"].input_per_million == pytest.approx(1.25)
    assert found["gpt 5 codex"].output_per_million == pytest.approx(10.0)
    assert found["gpt 5 codex"].cached_per_million == pytest.approx(0.125)


def test_skips_meters_that_do_not_price_a_chat_request() -> None:
    found = entries(GPT_5_PRO + NOISE)
    assert "gpt 5 pro batch" not in found
    assert not any("FT" in label or "Training" in label for label in found)


def test_keeps_regions_apart() -> None:
    rows = [
        meter("gpt 4.1 Inp glbl Tokens", 0.002, region="koreacentral"),
        meter("gpt 4.1 Outp glbl Tokens", 0.008, region="koreacentral"),
        meter("gpt 4.1 Inp glbl Tokens", 0.0022, region="brazilsouth"),
        meter("gpt 4.1 Outp glbl Tokens", 0.0088, region="brazilsouth"),
    ]
    found = list(StubAzure(rows).entries())
    assert len({entry.reference for entry in found}) == 2
    assert sorted(entry.input_per_million or 0 for entry in found) == pytest.approx([2.0, 2.2])


def test_a_search_term_never_matches_a_fragment_of_another_number() -> None:
    catalog = CompositeCatalog([StubAzure(GPT_41 + GPT_5_PRO + GPT_5_CODEX + NOISE)])
    labels = [entry.label for entry in catalog.search("gpt 5", region="koreacentral")]
    assert labels, "gpt-5 meters exist and must be findable"
    assert all(label.startswith("gpt 5") for label in labels), labels
    # "5" appears inside "0513"; that is not a match a reader would accept.
    assert "gpt 4o 0513" not in labels


def test_every_typed_term_has_to_land() -> None:
    catalog = CompositeCatalog([StubAzure(GPT_41 + GPT_5_CODEX)])
    labels = [entry.label for entry in catalog.search("gpt 5 codex")]
    assert labels == ["gpt 5 codex"]


def test_an_exact_token_outranks_a_prefix() -> None:
    rows = [
        meter("gpt 5 inp glbl Tokens", 0.001),
        meter("gpt 5 out glbl Tokens", 0.004),
    ] + GPT_5_PRO
    catalog = CompositeCatalog([StubAzure(rows)])
    labels = [entry.label for entry in catalog.search("gpt 5")]
    assert labels[0] == "gpt 5", labels


def test_an_unreachable_source_does_not_blank_the_others() -> None:
    class Broken:
        source = PriceSource.ANTHROPIC

        def entries(
            self, *, region: str | None = None, query: str | None = None
        ) -> list[CatalogEntry]:
            del region, query
            raise ValueError("pricing page moved")

    catalog = CompositeCatalog([Broken(), StubAzure(GPT_41)])
    assert [entry.label for entry in catalog.search("gpt 4.1")] == ["gpt 4.1"]


def test_the_typed_words_go_into_the_api_filter_not_a_local_pass() -> None:
    """Filtering locally meant guessing a product name, and the guess excluded gpt-5."""
    catalog = StubAzure(GPT_5_PRO)
    catalog.entries(query="gpt 5")
    assert "serviceName eq 'Foundry Models'" in catalog.last_filter
    assert "contains(meterName,'gpt 5')" in catalog.last_filter
    assert "contains(meterName,'gpt-5')" in catalog.last_filter
    # The bare first word matches tens of thousands of meters and earns a rate limit.
    assert "contains(meterName,'gpt')" not in catalog.last_filter
    assert "productName" not in catalog.last_filter


def test_browsing_without_a_term_stays_bounded() -> None:
    catalog = StubAzure(GPT_41)
    catalog.entries()
    assert "productName eq 'Azure OpenAI'" in catalog.last_filter
