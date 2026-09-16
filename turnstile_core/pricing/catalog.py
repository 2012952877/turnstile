"""Published list prices, read from the places the vendors publish them.

A catalogue answers three questions, in the order a person asks them: which models are priced,
how is this one priced, and what does this exact entry cost today. It never decides which entry
belongs to which registry model -- matching `gpt-4.1-mmai-foundry-f5efed78` against a meter named
`gpt 4.1 Inp glbl Tokens` is a guess, and a wrong guess here is invisible, because the bill still
renders and still adds up. So the mapping is chosen by a person once and stored, and this module
only resolves what was stored.

**Why a vocabulary and not a pattern.** Azure's retail catalogue holds twenty-one product lines
under `Foundry Models`, and each family writes its meter names differently:

    gpt 4.1 Inp glbl Tokens                 Azure OpenAI
    5.4 pp cd inp Dz 1M Tokens              Azure OpenAI GPT5 -- the model name is a bare version
    Phi-3.5-Mini-128K-Instruct-Output       Azure Phi -- no deployment segment at all
    FW GLM 5.2 Inp DZ Tokens                Azure Fireworks
    K2.5 Thinking Outp DZ Tokens            Azure Kimi
    4.6 Outp DZ L Tokens                    Azure Grok

An earlier version matched a sentence shape per family and recognised 281 of 1625 meters; the
rest were dropped without a sound, which reads as "this model has no published price" rather than
"this parser has not met this family". Sentence shapes are unbounded -- Microsoft adds a family
and the parser silently narrows. The vocabulary is not: a bucket word, an optional cached marker,
an optional deployment word, and everything else is the model's name. Scanning for the words
recognises 1037 of the same 1625 and, more importantly, *reports* what it could not read instead
of hiding it.
"""

from __future__ import annotations

import re
import time
import urllib.parse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

import httpx

from turnstile_core.domain.runtime_models import PriceSource

AZURE_RETAIL_ENDPOINT = "https://prices.azure.com/api/retail/prices"
ANTHROPIC_PRICING_URL = "https://docs.claude.com/en/docs/about-claude/pricing"
CATALOG_TTL_SECONDS = 6 * 60 * 60

# The index is read from one region because a model's Global rate is identical in every region --
# measured across 24 to 28 regions per model, always one figure. This region is chosen for
# breadth: it carries every model the narrower regions do.
INDEX_REGION = "eastus"

# --------------------------------------------------------------------------------------------
# The vocabulary
# --------------------------------------------------------------------------------------------

_BUCKET_INPUT = {"inp", "input"}
_BUCKET_OUTPUT = {"outp", "output", "out", "opt"}
_CACHED = {"cached", "cchd", "ccchd", "cd", "cache"}
_DEPLOYMENT = {
    "glbl": "Global",
    "gl": "Global",
    "global": "Global",
    "regnl": "Regional",
    "rgnl": "Regional",
    "regional": "Regional",
    "dz": "Data Zone",
    "dzn": "Data Zone",
    "dzl": "Data Zone",
    "dzone": "Data Zone",
    "datazone": "Data Zone",
}
# Words that make a meter something other than the rate a chat request is billed at. These are
# skipped deliberately, so they are not counted as unreadable.
_NOT_A_CHAT_RATE = {
    "batch", "ft", "finetuned", "hosting", "training", "rft", "grader",
    "provisioned", "managed", "reservation", "surcharge", "deployment",
}
# Units and qualifiers that carry no meaning once the bucket is known. `pp` and `L` mark
# provisioned and long-context variants that Azure prices identically to the base meter.
_NOISE = {"tokens", "1m", "1k", "pp", "l"}
# The only deployment word written as two words. Joined before tokenising so it is read as the
# deployment it is, rather than as two words that happen to sit next to each other.
_TWO_WORD_DEPLOYMENT = re.compile(r"\bdata\s+zone\b", re.IGNORECASE)

_DEPLOYMENT_ORDER = {"Global": 0, "Data Zone": 1, "Regional": 2}
UNSPECIFIED_DEPLOYMENT = "Standard"


@dataclass(frozen=True)
class CatalogModel:
    """One priceable model, named the way its vendor names it."""

    key: str
    label: str
    product: str
    source: PriceSource

    @property
    def search_text(self) -> str:
        return f"{self.label} {self.product}"


@dataclass(frozen=True)
class CatalogEntry:
    """One priceable thing, with every bucket the registry can charge for.

    A bucket is `None` when the vendor does not price it separately, which is different from
    free. Azure publishes no cache-write meter at all, so cache writes there keep the registry's
    existing fallback rather than being given an invented number.
    """

    reference: str
    label: str
    source: PriceSource
    detail: str | None = None
    input_per_million: float | None = None
    output_per_million: float | None = None
    cached_per_million: float | None = None
    cache_write_per_million: float | None = None

    @property
    def priced(self) -> bool:
        return self.input_per_million is not None and self.output_per_million is not None

    def discounted(self, percent: float | None) -> CatalogEntry:
        if percent is None or percent == 100:
            return self
        factor = percent / 100

        def apply(value: float | None) -> float | None:
            return None if value is None else round(value * factor, 8)

        return replace(
            self,
            input_per_million=apply(self.input_per_million),
            output_per_million=apply(self.output_per_million),
            cached_per_million=apply(self.cached_per_million),
            cache_write_per_million=apply(self.cache_write_per_million),
        )


@dataclass(frozen=True)
class CatalogOption:
    """How one model is priced under one deployment shape.

    `regions` lists every region that charges exactly these rates. When a shape charges the same
    everywhere -- which is what Global does, in every case measured -- there is nothing for a
    person to choose and `region_required` is false. Asking anyway would be asking for a decision
    that cannot change the answer.
    """

    reference: str
    deployment: str
    entry: CatalogEntry
    regions: tuple[str, ...]
    region_required: bool


@dataclass(frozen=True)
class CatalogOptions:
    """Everything known about one model's pricing, including what could not be read."""

    model: CatalogModel
    options: tuple[CatalogOption, ...] = ()
    unreadable: tuple[str, ...] = ()
    other_meters: tuple[str, ...] = ()
    note: str | None = None


@dataclass
class _ParsedMeter:
    stem: str
    slot: str
    deployment: str
    region: str
    per_million: float
    product: str


class PriceCatalog(Protocol):
    source: PriceSource

    def models(self) -> Sequence[CatalogModel]: ...

    def options(self, model: CatalogModel) -> CatalogOptions: ...

    def entry(self, reference: str) -> CatalogEntry | None: ...


def _http_get_json(url: str, *, timeout: float = 40.0) -> dict[str, Any]:
    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def parse_meter_name(name: str) -> tuple[str, str, str] | None:
    """`(stem, slot, deployment)` for a chat-token meter, or None.

    None means one of two different things, and the caller has to tell them apart: a meter that
    prices something other than a chat request -- batch, fine-tuning, hosting -- or one this
    vocabulary has not met. The first is expected; the second is a gap worth showing someone.
    """
    words = [word for word in re.split(r"[\s\-]+", _TWO_WORD_DEPLOYMENT.sub("DataZone", name))
             if word]
    if not words or words[-1].lower() != "tokens":
        return None
    lowered = [word.lower() for word in words]
    if any(word in _NOT_A_CHAT_RATE for word in lowered):
        return None

    slot: str | None = None
    deployment: str | None = None
    cached = False
    kept: list[str] = []
    for index, word in enumerate(lowered):
        if slot is None and word in _BUCKET_INPUT:
            slot = "input"
            if index and lowered[index - 1] in _CACHED:
                cached = True
            continue
        if slot is None and word in _BUCKET_OUTPUT:
            slot = "output"
            continue
        if deployment is None and word in _DEPLOYMENT:
            deployment = _DEPLOYMENT[word]
            continue
        if word in _CACHED or word in _NOISE:
            continue
        kept.append(words[index])

    if slot is None:
        return None
    stem = " ".join(kept).strip()
    if not stem:
        return None
    return stem, ("cached" if cached else slot), (deployment or UNSPECIFIED_DEPLOYMENT)


def prices_a_chat_request(name: str) -> bool:
    """Whether a meter is the sort of thing that could price a chat request at all."""
    normalised = _TWO_WORD_DEPLOYMENT.sub("DataZone", name)
    lowered = [word.lower() for word in re.split(r"[\s\-]+", normalised) if word]
    if not lowered or lowered[-1] != "tokens":
        return False
    return not any(word in _NOT_A_CHAT_RATE for word in lowered)


def _per_million(price: Any, unit: Any) -> float | None:
    if isinstance(price, bool) or not isinstance(price, int | float):
        return None
    text = str(unit or "").strip()
    # Azure quotes most token meters per 1K and the newer ones per 1M; the registry stores per 1M.
    scaled = float(price) * 1000 if text.startswith("1K") else float(price)
    # Rounded because multiplying a per-1K figure by 1000 in binary floating point turns 0.000138
    # into 0.13799999999999998, and a rate printed to fifteen decimals reads as a bug.
    return round(scaled, 8)


# --------------------------------------------------------------------------------------------
# Azure
# --------------------------------------------------------------------------------------------


class AzureRetailCatalog:
    """Azure's public retail price API.

    Queries are filtered server-side and kept narrow on purpose. An earlier version downloaded a
    product line and filtered locally, which meant guessing which products hold chat models --
    and the guess was wrong, because gpt-5 lives under `Azure OpenAI GPT5` rather than
    `Azure OpenAI`. A later version over-corrected into `contains(meterName,'gpt')`, matched tens
    of thousands of meters, earned a 429 partway through paging, and returned nothing at all.
    """

    source = PriceSource.AZURE_RETAIL

    def __init__(self, *, ttl_seconds: int = CATALOG_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._models: tuple[float, tuple[CatalogModel, ...]] | None = None
        self._options: dict[str, tuple[float, CatalogOptions]] = {}

    # -- index -------------------------------------------------------------------------------

    def models(self) -> Sequence[CatalogModel]:
        now = time.monotonic()
        if self._models is not None and now - self._models[0] < self._ttl:
            return self._models[1]
        built = tuple(self._build_index())
        self._models = (now, built)
        return built

    def _build_index(self) -> Iterable[CatalogModel]:
        rows = self._fetch(
            f"serviceName eq 'Foundry Models' and armRegionName eq '{INDEX_REGION}' "
            f"and contains(meterName,'Tokens')"
        )
        seen: dict[tuple[str, str], CatalogModel] = {}
        slots: dict[tuple[str, str], set[str]] = {}
        for row in rows:
            parsed = self._parse_row(row)
            if parsed is None:
                continue
            key = (parsed.product, parsed.stem)
            slots.setdefault(key, set()).add(parsed.slot)
            seen.setdefault(
                key,
                CatalogModel(
                    key=f"azure_retail:{parsed.product}:{parsed.stem}",
                    label=parsed.stem,
                    product=parsed.product,
                    source=PriceSource.AZURE_RETAIL,
                ),
            )
        # A model with only one side of the trade cannot price a request, so it is left out of
        # the picker rather than offered and then found wanting.
        return [model for key, model in sorted(seen.items()) if {"input", "output"} <= slots[key]]

    # -- one model ---------------------------------------------------------------------------

    def options(self, model: CatalogModel) -> CatalogOptions:
        now = time.monotonic()
        cached = self._options.get(model.key)
        if cached is not None and now - cached[0] < self._ttl:
            return cached[1]
        built = self._build_options(model)
        self._options[model.key] = (now, built)
        return built

    def _build_options(self, model: CatalogModel) -> CatalogOptions:
        clauses = [
            "serviceName eq 'Foundry Models'",
            f"productName eq '{_odata_literal(model.product)}'",
        ]
        name_filter = _meter_name_filter(model.label)
        if name_filter:
            clauses.append(name_filter)
        rows = self._fetch(" and ".join(clauses))

        buckets: dict[tuple[str, str], dict[str, float]] = {}
        unreadable: set[str] = set()
        other_meters: set[str] = set()
        for row in rows:
            name = str(row.get("meterName") or "")
            parsed = self._parse_row(row)
            if parsed is None:
                if not str(row.get("meterName") or "").strip():
                    continue
                if not prices_a_chat_request(name):
                    # Prices something other than a normal chat request: a batch rate, a
                    # fine-tuning rate, a per-hour reservation, a per-second video meter. Named
                    # rather than dropped, so the dialog can say "that one prices something
                    # else" instead of leaving a reader to wonder where it went.
                    if model.label.lower() in name.lower():
                        other_meters.add(f"{name} ({row.get('unitOfMeasure')})")
                elif model.label.lower() in name.lower():
                    unreadable.add(name)
                continue
            if parsed.stem != model.label:
                continue
            buckets.setdefault((parsed.deployment, parsed.region), {})[parsed.slot] = (
                parsed.per_million
            )

        Rates = tuple[float, float, float | None]
        by_deployment: dict[str, dict[Rates, list[str]]] = {}
        for (deployment, region), slot_prices in buckets.items():
            input_rate = slot_prices.get("input")
            output_rate = slot_prices.get("output")
            if input_rate is None or output_rate is None:
                continue
            rates: Rates = (input_rate, output_rate, slot_prices.get("cached"))
            by_deployment.setdefault(deployment, {}).setdefault(rates, []).append(region)

        options: list[CatalogOption] = []
        for deployment, groups in by_deployment.items():
            region_required = len(groups) > 1
            for rates, regions in groups.items():
                regions_sorted = tuple(sorted(regions))
                # A shape that charges one figure everywhere needs no region in its reference:
                # pinning one would make the stored mapping look region-specific when it is not.
                anchor = regions_sorted[0] if region_required else "*"
                options.append(
                    CatalogOption(
                        reference=(
                            f"azure_retail:{model.product}:{model.label}:{deployment}:{anchor}"
                        ),
                        deployment=deployment,
                        entry=CatalogEntry(
                            reference=(
                                f"azure_retail:{model.product}:{model.label}:"
                                f"{deployment}:{anchor}"
                            ),
                            label=model.label,
                            source=PriceSource.AZURE_RETAIL,
                            detail=deployment,
                            input_per_million=rates[0],
                            output_per_million=rates[1],
                            cached_per_million=rates[2],
                            cache_write_per_million=None,
                        ),
                        regions=regions_sorted,
                        region_required=region_required,
                    )
                )
        options.sort(
            key=lambda option: (
                _DEPLOYMENT_ORDER.get(option.deployment, 9),
                option.entry.input_per_million or 0,
            )
        )
        return CatalogOptions(
            model=model,
            options=tuple(options),
            unreadable=tuple(sorted(unreadable)),
            other_meters=tuple(sorted(other_meters)),
        )

    def entry(self, reference: str) -> CatalogEntry | None:
        parts = reference.split(":")
        if len(parts) != 5 or parts[0] != "azure_retail":
            return None
        _, product, stem, deployment, anchor = parts
        model = CatalogModel(
            key=f"azure_retail:{product}:{stem}",
            label=stem,
            product=product,
            source=PriceSource.AZURE_RETAIL,
        )
        for option in self.options(model).options:
            if option.deployment != deployment:
                continue
            if anchor == "*" and not option.region_required:
                return option.entry
            if anchor in option.regions:
                return option.entry
        return None

    # -- plumbing ----------------------------------------------------------------------------

    def _parse_row(self, row: dict[str, Any]) -> _ParsedMeter | None:
        name = str(row.get("meterName") or "")
        parsed = parse_meter_name(name)
        if parsed is None:
            return None
        per_million = _per_million(row.get("retailPrice"), row.get("unitOfMeasure"))
        if per_million is None:
            return None
        stem, slot, deployment = parsed
        return _ParsedMeter(
            stem=stem,
            slot=slot,
            deployment=deployment,
            region=str(row.get("armRegionName") or "global"),
            per_million=per_million,
            product=str(row.get("productName") or "Azure"),
        )

    def _fetch(self, filter_expression: str) -> list[dict[str, Any]]:
        url = (
            AZURE_RETAIL_ENDPOINT
            + "?currencyCode='USD'&$filter="
            + urllib.parse.quote(filter_expression)
        )
        rows: list[dict[str, Any]] = []
        for page in range(25):
            try:
                payload = _http_get_json(url)
            except httpx.HTTPError:
                # A page that fails partway through leaves what was already read. Returning
                # nothing instead would turn a throttled request into "this model has no
                # published price", which is a different and far more misleading answer.
                if page == 0:
                    raise
                break
            items = payload.get("Items")
            if isinstance(items, list):
                rows.extend(item for item in items if isinstance(item, dict))
            next_link = payload.get("NextPageLink")
            if not isinstance(next_link, str) or not next_link:
                break
            url = next_link
        return rows


def _odata_literal(value: str) -> str:
    return value.replace("'", "''")


def _meter_name_filter(label: str) -> str:
    """An OData clause matching a model's name against a meter name.

    Meter names separate words with spaces in one family and hyphens in another -- `gpt 5 pro`
    beside `gpt-5-codex` -- so both spellings are tried. The words are never sent one at a time:
    `contains(meterName,'gpt')` matches tens of thousands of meters and gets the request
    throttled long before it finishes paging.
    """
    terms = [term for term in re.split(r"[^A-Za-z0-9.]+", label.strip()) if term]
    if not terms:
        return ""
    joined = terms[:4]
    variants = {" ".join(joined), "-".join(joined)}
    clauses = [
        f"contains(meterName,'{_odata_literal(value)}')" for value in sorted(variants)
    ]
    return "(" + " or ".join(clauses) + ")" if clauses else ""


# --------------------------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------------------------

_ANTHROPIC_MODEL = re.compile(r"^Claude\s+[A-Z][A-Za-z]*\s+[\d.]+", re.IGNORECASE)
_ANTHROPIC_PRICE = re.compile(r"^\$\s?([\d,]+(?:\.\d+)?)\s*/\s*MTok$", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_DROP = re.compile(r"<(script|style).*?</\1>", re.IGNORECASE | re.DOTALL)
ANTHROPIC_PRODUCT = "Anthropic"


class AnthropicCatalog:
    """Anthropic's published list, parsed from the pricing page.

    The page prints five figures per model in a fixed order -- input, cache write at the
    5-minute TTL, cache write at the 1-hour TTL, cache read, output -- and those figures are
    fixed multiples of the input rate (1.25x, 2x, 0.1x). The multiples are checked on parse: if
    the page is restructured the arithmetic stops holding, and a row that fails the check is
    dropped rather than published as a price. That is the whole defence against a layout change
    quietly rewriting someone's bill.
    """

    source = PriceSource.ANTHROPIC

    def __init__(self, *, ttl_seconds: int = CATALOG_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._cache: tuple[float, tuple[CatalogEntry, ...]] | None = None

    def _entries(self) -> Sequence[CatalogEntry]:
        now = time.monotonic()
        if self._cache is not None and now - self._cache[0] < self._ttl:
            return self._cache[1]
        built = tuple(self._build())
        self._cache = (now, built)
        return built

    def models(self) -> Sequence[CatalogModel]:
        return [
            CatalogModel(
                key=f"anthropic:{ANTHROPIC_PRODUCT}:{entry.label}",
                label=entry.label,
                product=ANTHROPIC_PRODUCT,
                source=PriceSource.ANTHROPIC,
            )
            for entry in self._entries()
        ]

    def options(self, model: CatalogModel) -> CatalogOptions:
        for entry in self._entries():
            if entry.label != model.label:
                continue
            reference = f"anthropic:{ANTHROPIC_PRODUCT}:{entry.label}:List price:*"
            return CatalogOptions(
                model=model,
                options=(
                    CatalogOption(
                        reference=reference,
                        deployment="List price",
                        entry=replace(entry, reference=reference, detail="List price"),
                        regions=(),
                        region_required=False,
                    ),
                ),
                note="Anthropic 对所有区域发布同一份列表价。",
            )
        return CatalogOptions(model=model)

    def entry(self, reference: str) -> CatalogEntry | None:
        parts = reference.split(":")
        if len(parts) != 5 or parts[0] != "anthropic":
            return None
        label = parts[2]
        for item in self._entries():
            if item.label == label:
                return replace(item, reference=reference, detail="List price")
        return None

    def _build(self) -> Iterable[CatalogEntry]:
        response = httpx.get(
            ANTHROPIC_PRICING_URL,
            timeout=40.0,
            follow_redirects=True,
            headers={"User-Agent": "turnstile-price-sync"},
        )
        response.raise_for_status()
        lines = _visible_lines(response.text)
        seen: set[str] = set()
        for index, line in enumerate(lines):
            if len(line) > 48 or not _ANTHROPIC_MODEL.match(line):
                continue
            name = line.split("(")[0].strip()
            if name in seen:
                continue
            figures = _following_prices(lines, index + 1, count=5)
            if len(figures) < 5:
                continue
            base, write_5m, write_1h, read, output = figures
            if not _multiples_hold(base, write_5m, write_1h, read):
                continue
            seen.add(name)
            yield CatalogEntry(
                reference=f"anthropic:{ANTHROPIC_PRODUCT}:{name}:List price:*",
                label=name,
                source=PriceSource.ANTHROPIC,
                detail="List price",
                input_per_million=base,
                output_per_million=output,
                cached_per_million=read,
                # The 5-minute TTL is what a request gets unless it asks for the hourly one.
                cache_write_per_million=write_5m,
            )


def _visible_lines(html: str) -> list[str]:
    text = _TAG.sub("\n", _DROP.sub(" ", html))
    return [line.strip() for line in text.split("\n") if line.strip()]


def _following_prices(lines: Sequence[str], start: int, *, count: int) -> list[float]:
    found: list[float] = []
    for line in lines[start : start + 40]:
        match = _ANTHROPIC_PRICE.match(line)
        if match is not None:
            found.append(float(match.group(1).replace(",", "")))
            if len(found) == count:
                break
            continue
        if _ANTHROPIC_MODEL.match(line) and len(line) <= 48:
            break
    return found


def _multiples_hold(base: float, write_5m: float, write_1h: float, read: float) -> bool:
    if base <= 0:
        return False

    def close(value: float, expected: float) -> bool:
        return abs(value - expected) <= max(expected * 0.02, 0.005)

    return close(write_5m, base * 1.25) and close(write_1h, base * 2) and close(read, base * 0.1)


# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelSearch:
    models: tuple[CatalogModel, ...] = ()
    unavailable: tuple[str, ...] = field(default=())


class CompositeCatalog:
    """Every source behind one lookup, because a connection can serve several vendors."""

    def __init__(self, catalogs: Sequence[PriceCatalog]) -> None:
        self._catalogs = list(catalogs)

    def search_models(self, query: str, *, limit: int = 60) -> ModelSearch:
        terms = [term for term in re.split(r"[^a-z0-9.]+", query.lower()) if term]
        scored: list[tuple[int, int, str, CatalogModel]] = []
        unavailable: list[str] = []
        for catalog in self._catalogs:
            try:
                candidates = catalog.models()
            except (httpx.HTTPError, ValueError):
                # One unreachable source must not blank the others: a Claude price that cannot be
                # read is a reason to leave Claude alone, not to stop pricing GPT.
                unavailable.append(str(getattr(catalog, "source", "unknown")))
                continue
            for model in candidates:
                haystack = model.search_text.lower()
                tokens = [token for token in re.split(r"[^a-z0-9.]+", haystack) if token]
                scores = [_term_score(term, tokens) for term in terms]
                if terms and not all(scores):
                    continue
                scored.append((-sum(scores), len(model.label), model.label.lower(), model))
        scored.sort(key=lambda item: item[:3])
        return ModelSearch(
            models=tuple(model for *_, model in scored[:limit]),
            unavailable=tuple(unavailable),
        )

    def options(self, model_key: str) -> CatalogOptions | None:
        for catalog in self._catalogs:
            try:
                for model in catalog.models():
                    if model.key == model_key:
                        return catalog.options(model)
            except (httpx.HTTPError, ValueError):
                continue
        return None

    def lookup(self, reference: str) -> CatalogEntry | None:
        for catalog in self._catalogs:
            try:
                found = catalog.entry(reference)
            except (httpx.HTTPError, ValueError):
                continue
            if found is not None:
                return found
        return None


def _term_score(term: str, tokens: Sequence[str]) -> int:
    """2 for an exact token, 1 for a token that starts with the term, 0 for no match.

    Never a fragment buried inside another number: substring matching made `gpt 5` find
    `gpt 4o 0513`, because "5" appears inside "0513" -- a match no reader would call one.
    """
    if term in tokens:
        return 2
    return 1 if any(token.startswith(term) for token in tokens) else 0


def build_default_catalog() -> CompositeCatalog:
    return CompositeCatalog([AzureRetailCatalog(), AnthropicCatalog()])
