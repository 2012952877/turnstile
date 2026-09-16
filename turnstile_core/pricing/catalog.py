"""Published list prices, read from the places the vendors publish them.

A catalog answers two questions: "what entries look like this model?" (so a person can pick the
right one once) and "what does this entry cost today?" (so the sync can keep the number current).
It never decides which entry belongs to which model. Matching a registry alias like
`gpt-4.1-mmai-foundry-f5efed78` against a meter named `gpt 4.1 Inp glbl Tokens` is a guess, and a
wrong guess here is invisible: the bill still looks plausible. So the mapping is stored, and this
module only resolves what was stored.

Two sources, because the two model families publish in different places:

* Azure's retail price API covers the models Microsoft sells directly. It is a real API -- public,
  unauthenticated, structured, per-region -- and its numbers match the public pricing page.
* Anthropic publishes a documentation page and nothing machine-readable, so that one is parsed.
  Everything downstream treats it as the more fragile of the two.
"""

from __future__ import annotations

import re
import time
import urllib.parse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

import httpx

from turnstile_core.domain.runtime_models import PriceSource

AZURE_RETAIL_ENDPOINT = "https://prices.azure.com/api/retail/prices"
ANTHROPIC_PRICING_URL = "https://docs.claude.com/en/docs/about-claude/pricing"
CATALOG_TTL_SECONDS = 6 * 60 * 60


@dataclass(frozen=True)
class CatalogEntry:
    """One priceable thing, with every bucket the registry can charge for.

    A bucket is `None` when the vendor does not price it separately, which is different from
    free. Azure has no cache-write meter at all, so cache writes there fall back to the cached
    rate exactly as they did before any of this existed.
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


class PriceCatalog(Protocol):
    source: PriceSource

    def entries(self, *, region: str | None = None) -> Sequence[CatalogEntry]: ...


def _http_get_json(url: str, *, timeout: float = 30.0) -> dict[str, Any]:
    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


# --------------------------------------------------------------------------------------------
# Azure
# --------------------------------------------------------------------------------------------

# Meter names read like `gpt 4.1 cached Inp glbl Tokens`: a model stem, an optional `cached`, the
# bucket, the deployment shape, then the literal `Tokens`. Anything that does not fit this shape
# is skipped rather than guessed at -- fine-tuning, batch, audio and realtime meters all exist in
# the same product and none of them price a chat request.
_AZURE_METER = re.compile(
    r"^(?P<stem>.+?)\s+(?P<cached>cached\s+)?(?P<bucket>Inp|Input|Outp|Output)\s+"
    r"(?P<deployment>glbl|global|regnl|regional|DataZone|Data Zone)\s+Tokens$",
    re.IGNORECASE,
)
_AZURE_EXCLUDE = re.compile(
    r"\b(ft|fine|train|batch|prvw|preview|rt|realtime|aud|audio|img|image|"
    r"grdr|mdl|surcharge|session|calls|priority)\b",
    re.IGNORECASE,
)
_DEPLOYMENT_LABEL = {
    "glbl": "Global",
    "global": "Global",
    "regnl": "Regional",
    "regional": "Regional",
    "datazone": "Data Zone",
    "data zone": "Data Zone",
}


class AzureRetailCatalog:
    """Azure's public retail price API.

    Filtering by `contains(meterName, ...)` rather than by product is deliberate. Filtering by
    product alone returns thousands of rows across every region, and any caller that stops
    paging early gets a partial set that differs run to run -- which is how you end up concluding
    a model has no published price when it has one.
    """

    source = PriceSource.AZURE_RETAIL

    def __init__(self, *, ttl_seconds: int = CATALOG_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, tuple[CatalogEntry, ...]]] = {}

    def entries(self, *, region: str | None = None) -> Sequence[CatalogEntry]:
        key = region or "*"
        cached = self._cache.get(key)
        now = time.monotonic()
        if cached is not None and now - cached[0] < self._ttl:
            return cached[1]
        built = tuple(self._build(region))
        self._cache[key] = (now, built)
        return built

    def _build(self, region: str | None) -> Iterable[CatalogEntry]:
        clauses = ["productName eq 'Azure OpenAI'", "contains(meterName,'Tokens')"]
        if region:
            clauses.append(f"armRegionName eq '{region}'")
        rows = self._fetch(" and ".join(clauses))
        # A model's buckets arrive as separate meters, so they are assembled here into the one
        # entry a person actually picks.
        grouped: dict[tuple[str, str], dict[str, float]] = {}
        for row in rows:
            name = str(row.get("meterName") or "")
            if _AZURE_EXCLUDE.search(name):
                continue
            match = _AZURE_METER.match(name)
            if match is None:
                continue
            price = row.get("retailPrice")
            unit = str(row.get("unitOfMeasure") or "")
            if not isinstance(price, int | float):
                continue
            # Azure quotes per 1K; the registry stores per 1M.
            per_million = float(price) * 1000 if unit.strip().startswith("1K") else float(price)
            stem = match.group("stem").strip()
            deployment = match.group("deployment").strip().lower()
            bucket = match.group("bucket").lower()
            slot = (
                "cached"
                if match.group("cached")
                else "input"
                if bucket in {"inp", "input"}
                else "output"
            )
            grouped.setdefault((stem, deployment), {})[slot] = per_million

        for (stem, deployment), buckets in sorted(grouped.items()):
            label_deployment = _DEPLOYMENT_LABEL.get(deployment, deployment)
            detail = label_deployment if region is None else f"{label_deployment} · {region}"
            yield CatalogEntry(
                reference=f"azure_retail:{region or 'any'}:{stem}:{deployment}",
                label=stem,
                source=PriceSource.AZURE_RETAIL,
                detail=detail,
                input_per_million=buckets.get("input"),
                output_per_million=buckets.get("output"),
                cached_per_million=buckets.get("cached"),
                # Azure publishes no cache-write meter. Left as None so the registry keeps its
                # existing fallback (cache writes bill at the cached rate) instead of inventing
                # a number.
                cache_write_per_million=None,
            )

    def _fetch(self, filter_expression: str) -> list[dict[str, Any]]:
        url = (
            AZURE_RETAIL_ENDPOINT
            + "?currencyCode='USD'&$filter="
            + urllib.parse.quote(filter_expression)
        )
        rows: list[dict[str, Any]] = []
        # Bounded rather than unbounded: a filter that accidentally matches everything should
        # fail loudly on incompleteness, not spend the afternoon paging.
        for _ in range(60):
            payload = _http_get_json(url)
            items = payload.get("Items")
            if isinstance(items, list):
                rows.extend(item for item in items if isinstance(item, dict))
            next_link = payload.get("NextPageLink")
            if not isinstance(next_link, str) or not next_link:
                break
            url = next_link
        return rows


# --------------------------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------------------------

_ANTHROPIC_MODEL = re.compile(r"^Claude\s+[A-Z][A-Za-z]*\s+[\d.]+", re.IGNORECASE)
_ANTHROPIC_PRICE = re.compile(r"^\$\s?([\d,]+(?:\.\d+)?)\s*/\s*MTok$", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_DROP = re.compile(r"<(script|style).*?</\1>", re.IGNORECASE | re.DOTALL)


class AnthropicCatalog:
    """Anthropic's published list, parsed from the pricing page.

    The page prints five figures per model in a fixed order -- input, cache write at the
    5-minute TTL, cache write at the 1-hour TTL, cache read, output -- and those figures are
    fixed multiples of the input rate (1.25x, 2x, 0.1x). The multiples are checked on parse:
    if the page is restructured the arithmetic stops holding, and a row that fails the check is
    dropped rather than published as a price. That is the whole defence against a layout change
    quietly rewriting someone's bill.
    """

    source = PriceSource.ANTHROPIC

    def __init__(self, *, ttl_seconds: int = CATALOG_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._cache: tuple[float, tuple[CatalogEntry, ...]] | None = None

    def entries(self, *, region: str | None = None) -> Sequence[CatalogEntry]:
        del region  # Anthropic publishes one list, not one per region.
        now = time.monotonic()
        if self._cache is not None and now - self._cache[0] < self._ttl:
            return self._cache[1]
        built = tuple(self._build())
        self._cache = (now, built)
        return built

    def _build(self) -> Iterable[CatalogEntry]:
        response = httpx.get(
            ANTHROPIC_PRICING_URL,
            timeout=30.0,
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
                reference=f"anthropic:{name}",
                label=name,
                source=PriceSource.ANTHROPIC,
                detail="Anthropic list price",
                input_per_million=base,
                output_per_million=output,
                cached_per_million=read,
                # The 5-minute TTL is the default a request gets unless it asks for the hourly
                # one, so that is the rate the registry charges cache writes at.
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
        # Another model heading before the row is complete means the table shape changed.
        if _ANTHROPIC_MODEL.match(line) and len(line) <= 48:
            break
    return found


def _multiples_hold(base: float, write_5m: float, write_1h: float, read: float) -> bool:
    if base <= 0:
        return False

    def close(value: float, expected: float) -> bool:
        return abs(value - expected) <= max(expected * 0.02, 0.005)

    return (
        close(write_5m, base * 1.25) and close(write_1h, base * 2) and close(read, base * 0.1)
    )


# --------------------------------------------------------------------------------------------


class CompositeCatalog:
    """Every source behind one lookup, because a single connection serves several vendors."""

    def __init__(self, catalogs: Sequence[PriceCatalog]) -> None:
        self._catalogs = list(catalogs)

    def search(
        self, query: str, *, region: str | None = None, limit: int = 40
    ) -> list[CatalogEntry]:
        terms = [term for term in re.split(r"[^a-z0-9.]+", query.lower()) if term]
        scored: list[tuple[int, int, str, CatalogEntry]] = []
        for entry in self._all(region):
            if not entry.priced:
                continue
            label = entry.label.lower()
            # Whole-token matching, not substring: searching `opus 5` must not rank
            # `Claude Opus 4.5` above `Claude Opus 5` just because "5" appears inside "4.5".
            tokens = set(re.split(r"[^a-z0-9.]+", label)) - {""}
            score = sum(2 if term in tokens else 1 if term in label else 0 for term in terms)
            if terms and score == 0:
                continue
            scored.append((-score, len(label), label, entry))
        scored.sort(key=lambda item: item[:3])
        return [entry for *_, entry in scored[:limit]]

    def lookup(self, reference: str, *, region: str | None = None) -> CatalogEntry | None:
        # An Azure reference carries the region it was priced in, so a stored mapping resolves
        # without the caller having to remember which region the connection lives in.
        if region is None and reference.startswith("azure_retail:"):
            parts = reference.split(":")
            if len(parts) >= 2 and parts[1] not in {"", "any"}:
                region = parts[1]
        for entry in self._all(region):
            if entry.reference == reference:
                return entry
        return None

    def _all(self, region: str | None) -> list[CatalogEntry]:
        collected: list[CatalogEntry] = []
        for catalog in self._catalogs:
            try:
                collected.extend(catalog.entries(region=region))
            except (httpx.HTTPError, ValueError):
                # One unreachable source must not blank the others: a Claude price that cannot
                # be read is a reason to leave that model alone, not to stop pricing GPT.
                continue
        return collected


def build_default_catalog() -> CompositeCatalog:
    return CompositeCatalog([AzureRetailCatalog(), AnthropicCatalog()])
