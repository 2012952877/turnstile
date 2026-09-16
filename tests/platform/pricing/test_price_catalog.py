"""The catalogue's three jobs: read a vendor's meter names, group a model's prices, resolve one.

Every fixture below is a meter name copied verbatim from the Azure retail price API. The naming
is not consistent between product lines, and each inconsistency is a chance to drop a model's
price without anyone noticing -- which is exactly what a shape-matching parser did before this.
"""

from __future__ import annotations

from typing import Any

import pytest

from turnstile_core.domain.runtime_models import PriceSource
from turnstile_core.pricing.catalog import (
    AzureRetailCatalog,
    CatalogModel,
    CatalogOptions,
    CompositeCatalog,
    parse_meter_name,
)


def meter(name: str, price: float, region: str = "eastus",
          product: str = "Azure OpenAI", unit: str = "1K") -> dict[str, Any]:
    return {
        "meterName": name,
        "retailPrice": price,
        "unitOfMeasure": unit,
        "armRegionName": region,
        "productName": product,
        "serviceName": "Foundry Models",
    }


class StubAzure(AzureRetailCatalog):
    """Same parsing and grouping, no network."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__()
        self._rows = rows
        self.filters: list[str] = []

    def _fetch(self, filter_expression: str) -> list[dict[str, Any]]:
        self.filters.append(filter_expression)
        return self._rows


# --------------------------------------------------------------------------------------------
# The vocabulary
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # Azure OpenAI
        ("gpt 4.1 Inp glbl Tokens", ("gpt 4.1", "input", "Global")),
        ("gpt 4.1 Outp glbl Tokens", ("gpt 4.1", "output", "Global")),
        ("gpt 4.1 cached Inp glbl Tokens", ("gpt 4.1", "cached", "Global")),
        ("gpt 4.1 Inp Data Zone Tokens", ("gpt 4.1", "input", "Data Zone")),
        ("gpt 4.1 Inp regnl Tokens", ("gpt 4.1", "input", "Regional")),
        # Azure OpenAI GPT5 -- the model name is a bare version, `opt` means output,
        # `cd` means cached, `Gl`/`Dz` are the deployment.
        ("5.4 pp inp Gl 1M Tokens", ("5.4", "input", "Global")),
        ("5.4 pp opt Gl 1M Tokens", ("5.4", "output", "Global")),
        ("5.1 pp cd inp Dz 1M Tokens", ("5.1", "cached", "Data Zone")),
        ("gpt 5 pro out glbl Tokens", ("gpt 5 pro", "output", "Global")),
        ("gpt-5-codex-inp-glbl Tokens", ("gpt 5 codex", "input", "Global")),
        ("gpt-5-codex-ccchd-inp-glbl Tokens", ("gpt 5 codex", "cached", "Global")),
        # Other vendors, each with its own spelling.
        ("FW GLM 5.2 Inp DZ Tokens", ("FW GLM 5.2", "input", "Data Zone")),
        ("K2.5 Thinking Outp DZ Tokens", ("K2.5 Thinking", "output", "Data Zone")),
        ("4.6 Outp DZ L Tokens", ("4.6", "output", "Data Zone")),
        ("V3.1 Inp DZone Tokens", ("V3.1", "input", "Data Zone")),
        # No deployment segment at all.
        ("Phi-3.5-Mini-128K-Instruct-Output Tokens",
         ("Phi 3.5 Mini 128K Instruct", "output", "Standard")),
    ],
)
def test_reads_every_naming_convention_in_the_catalogue(
    name: str, expected: tuple[str, str, str]
) -> None:
    assert parse_meter_name(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "gpt 4o 0513 Batch Inp glbl Tokens",
        "o4-mini-ft model grader output Tokens",
        "Phi-3-Mini-128K-Instruct-Input-Finetuned Tokens",
        "Provisioned Managed Global Unit",
        "Sora 2 pro glbl Second",
        "gpt-transcribe Gl Unit",
    ],
)
def test_declines_meters_that_do_not_price_a_chat_request(name: str) -> None:
    assert parse_meter_name(name) is None


# --------------------------------------------------------------------------------------------
# Grouping
# --------------------------------------------------------------------------------------------

GPT_41 = [
    meter("gpt 4.1 Inp glbl Tokens", 0.002, region=region)
    for region in ("eastus", "brazilsouth", "canadaeast")
] + [
    meter("gpt 4.1 Outp glbl Tokens", 0.008, region=region)
    for region in ("eastus", "brazilsouth", "canadaeast")
] + [
    meter("gpt 4.1 cached Inp glbl Tokens", 0.0005, region=region)
    for region in ("eastus", "brazilsouth", "canadaeast")
] + [
    # Regional genuinely differs by region, which is the case that needs a third choice.
    meter("gpt 4.1 Inp regnl Tokens", 0.0022, region="eastus"),
    meter("gpt 4.1 Outp regnl Tokens", 0.0088, region="eastus"),
    meter("gpt 4.1 Inp regnl Tokens", 0.0024, region="northeurope"),
    meter("gpt 4.1 Outp regnl Tokens", 0.0096, region="northeurope"),
]

GPT_41_MODEL = CatalogModel(
    key="azure_retail:Azure OpenAI:gpt 4.1",
    label="gpt 4.1",
    product="Azure OpenAI",
    source=PriceSource.AZURE_RETAIL,
)


def options_for(rows: list[dict[str, Any]], model: CatalogModel) -> CatalogOptions:
    return StubAzure(rows).options(model)


def test_a_shape_priced_the_same_everywhere_asks_for_no_region() -> None:
    found = options_for(GPT_41, GPT_41_MODEL)
    globals_ = [option for option in found.options if option.deployment == "Global"]
    assert len(globals_) == 1, "three regions charging one figure is one choice, not three"
    option = globals_[0]
    assert option.region_required is False
    assert option.reference.endswith(":Global:*")
    assert option.regions == ("brazilsouth", "canadaeast", "eastus")
    assert option.entry.input_per_million == pytest.approx(2.0)
    assert option.entry.output_per_million == pytest.approx(8.0)
    assert option.entry.cached_per_million == pytest.approx(0.5)


def test_a_shape_whose_price_varies_does_ask_for_a_region() -> None:
    found = options_for(GPT_41, GPT_41_MODEL)
    regional = [option for option in found.options if option.deployment == "Regional"]
    assert len(regional) == 2
    assert all(option.region_required for option in regional)
    assert {option.regions for option in regional} == {("eastus",), ("northeurope",)}
    assert sorted(option.entry.input_per_million or 0 for option in regional) == pytest.approx(
        [2.2, 2.4]
    )


def test_options_are_ordered_so_the_usual_choice_comes_first() -> None:
    found = options_for(GPT_41, GPT_41_MODEL)
    assert [option.deployment for option in found.options][0] == "Global"


def test_a_meter_it_cannot_read_is_reported_not_dropped() -> None:
    rows = GPT_41 + [meter("gpt 4.1 weirdly-worded glbl Tokens", 0.003)]
    found = options_for(rows, GPT_41_MODEL)
    assert "gpt 4.1 weirdly-worded glbl Tokens" in found.unreadable


def test_a_meter_billed_in_something_else_says_so() -> None:
    rows = GPT_41 + [meter("gpt 4.1 hosting global Unit", 1.5, unit="1 Hour")]
    found = options_for(rows, GPT_41_MODEL)
    assert any("gpt 4.1 hosting global Unit" in item for item in found.other_meters)
    assert not found.unreadable


def test_per_million_meters_are_not_multiplied_again() -> None:
    rows = [
        meter("5.4 pp inp Gl 1M Tokens", 1.25, product="Azure OpenAI GPT5", unit="1M"),
        meter("5.4 pp opt Gl 1M Tokens", 10.0, product="Azure OpenAI GPT5", unit="1M"),
    ]
    model = CatalogModel(
        key="azure_retail:Azure OpenAI GPT5:5.4",
        label="5.4",
        product="Azure OpenAI GPT5",
        source=PriceSource.AZURE_RETAIL,
    )
    option = options_for(rows, model).options[0]
    assert option.entry.input_per_million == pytest.approx(1.25)
    assert option.entry.output_per_million == pytest.approx(10.0)


# --------------------------------------------------------------------------------------------
# The index and the search
# --------------------------------------------------------------------------------------------


def test_the_index_lists_each_model_once_and_only_if_it_can_be_priced() -> None:
    rows = GPT_41 + [
        # Output only: cannot price a request, so it is not offered.
        meter("gpt 4.1 orphan Outp glbl Tokens", 0.01),
    ]
    models = list(StubAzure(rows).models())
    assert [model.label for model in models] == ["gpt 4.1"]


def test_a_term_never_matches_a_fragment_of_another_number() -> None:
    rows = [
        meter("gpt 5 pro inp glbl Tokens", 0.015, product="Azure OpenAI GPT5"),
        meter("gpt 5 pro out glbl Tokens", 0.12, product="Azure OpenAI GPT5"),
        meter("gpt 4o 0513 Input global Tokens", 0.005),
        meter("gpt 4o 0513 Output global Tokens", 0.015),
    ]
    found = CompositeCatalog([StubAzure(rows)]).search_models("gpt 5")
    labels = [model.label for model in found.models]
    assert labels == ["gpt 5 pro"], labels


def test_every_typed_term_has_to_land() -> None:
    rows = GPT_41 + [
        meter("gpt 4.1 mini Inp glbl Tokens", 0.0004),
        meter("gpt 4.1 mini Outp glbl Tokens", 0.0016),
    ]
    found = CompositeCatalog([StubAzure(rows)]).search_models("gpt 4.1 mini")
    assert [model.label for model in found.models] == ["gpt 4.1 mini"]


def test_the_typed_words_go_into_the_api_filter() -> None:
    """Filtering locally meant guessing a product name, and the guess excluded gpt-5."""
    catalog = StubAzure(GPT_41)
    catalog.options(GPT_41_MODEL)
    sent = catalog.filters[-1]
    assert "serviceName eq 'Foundry Models'" in sent
    assert "productName eq 'Azure OpenAI'" in sent
    assert "contains(meterName,'gpt 4.1')" in sent
    assert "contains(meterName,'gpt-4.1')" in sent
    # The bare first word matches tens of thousands of meters and earns a rate limit.
    assert "contains(meterName,'gpt')" not in sent


def test_an_unreachable_source_is_named_rather_than_pretended_empty() -> None:
    class Broken:
        source = PriceSource.ANTHROPIC

        def models(self) -> list[CatalogModel]:
            raise ValueError("pricing page moved")

        def options(self, model: CatalogModel) -> CatalogOptions:
            raise ValueError("pricing page moved")

        def entry(self, reference: str) -> None:
            return None

    found = CompositeCatalog([Broken(), StubAzure(GPT_41)]).search_models("gpt 4.1")
    assert [model.label for model in found.models] == ["gpt 4.1"]
    assert found.unavailable == (str(PriceSource.ANTHROPIC),)


# --------------------------------------------------------------------------------------------
# Resolving a stored mapping
# --------------------------------------------------------------------------------------------


def test_a_stored_reference_resolves_back_to_the_same_rates() -> None:
    catalog = StubAzure(GPT_41)
    chosen = next(
        option for option in catalog.options(GPT_41_MODEL).options
        if option.deployment == "Global"
    )
    resolved = catalog.entry(chosen.reference)
    assert resolved is not None
    assert resolved.input_per_million == chosen.entry.input_per_million
    assert resolved.output_per_million == chosen.entry.output_per_million


def test_a_region_pinned_reference_resolves_to_that_region_only() -> None:
    catalog = StubAzure(GPT_41)
    northeurope = next(
        option for option in catalog.options(GPT_41_MODEL).options
        if option.regions == ("northeurope",)
    )
    resolved = catalog.entry(northeurope.reference)
    assert resolved is not None
    assert resolved.input_per_million == pytest.approx(2.4)


def test_a_reference_that_no_longer_exists_resolves_to_nothing() -> None:
    catalog = StubAzure(GPT_41)
    assert catalog.entry("azure_retail:Azure OpenAI:gpt 4.1:Regional:antarctica") is None
    assert catalog.entry("nonsense") is None
