"""Turn published list prices into the rates the registry charges.

The arithmetic is trivial -- list price times discount. Everything interesting here is about
refusing to write a number when there is any doubt about it, because a wrong rate is silent: the
dashboard still renders, the bill still adds up, and nobody notices until someone reconciles it
against an invoice weeks later.

So: a model whose mapping resolves to nothing is marked `unmapped` and keeps its current rates. A
source that cannot be read leaves the rates alone and marks the row `stale`. A list price that
moved further than the review threshold is reported as `review_needed` and, again, does not write.
Only an unambiguous, small-enough change writes.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from turnstile_core.domain.runtime_models import ManagedModel, PriceSource, PriceSyncStatus
from turnstile_core.pricing.catalog import CatalogEntry, CompositeCatalog

# A published rate rarely moves more than a few percent. Twenty is generous enough that ordinary
# repricing passes unattended, and tight enough that a parser reading the wrong column stops.
DEFAULT_REVIEW_THRESHOLD = 0.20


@dataclass(frozen=True)
class ModelPriceUpdate:
    """What the sync decided for one model. `writes` is False when only the status changes."""

    model_id: UUID
    model_key: str
    status: PriceSyncStatus
    message: str | None = None
    writes: bool = False
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    cached_cost_per_million: float | None = None
    cache_write_cost_per_million: float | None = None
    list_input_cost_per_million: float | None = None
    list_output_cost_per_million: float | None = None
    list_cached_cost_per_million: float | None = None
    list_cache_write_cost_per_million: float | None = None


@dataclass(frozen=True)
class SyncSummary:
    updates: tuple[ModelPriceUpdate, ...]

    @property
    def written(self) -> int:
        return sum(1 for update in self.updates if update.writes)

    @property
    def unmapped(self) -> int:
        return sum(1 for u in self.updates if u.status is PriceSyncStatus.UNMAPPED)

    @property
    def review_needed(self) -> int:
        return sum(1 for u in self.updates if u.status is PriceSyncStatus.REVIEW_NEEDED)

    @property
    def stale(self) -> int:
        return sum(1 for u in self.updates if u.status is PriceSyncStatus.STALE)


def plan_price_sync(
    models: Iterable[ManagedModel],
    catalog: CompositeCatalog,
    *,
    only: Sequence[UUID] | None = None,
    review_threshold: float = DEFAULT_REVIEW_THRESHOLD,
) -> SyncSummary:
    selected = set(only) if only is not None else None
    updates: list[ModelPriceUpdate] = []
    for model in models:
        if selected is not None and model.id not in selected:
            continue
        # A model priced by hand is left exactly as it is. That is the contract that lets this
        # feature ship without touching anyone's existing rates.
        if model.price_source is PriceSource.MANUAL:
            continue
        updates.append(_plan_one(model, catalog, review_threshold))
    return SyncSummary(updates=tuple(updates))


def _plan_one(
    model: ManagedModel, catalog: CompositeCatalog, review_threshold: float
) -> ModelPriceUpdate:
    reference = model.price_reference or ""
    try:
        entry = catalog.lookup(reference)
    except Exception as error:  # noqa: BLE001 - any source failure means "keep what we have"
        return ModelPriceUpdate(
            model_id=model.id,
            model_key=model.model_key,
            status=PriceSyncStatus.STALE,
            message=f"价目表读取失败，保留现有单价：{type(error).__name__}",
        )
    if entry is None:
        return ModelPriceUpdate(
            model_id=model.id,
            model_key=model.model_key,
            status=PriceSyncStatus.UNMAPPED,
            message=f"价目表中找不到 {reference}，保留现有单价",
        )
    if not entry.priced:
        return ModelPriceUpdate(
            model_id=model.id,
            model_key=model.model_key,
            status=PriceSyncStatus.UNMAPPED,
            message=f"{entry.label} 未同时发布输入与输出价，保留现有单价",
        )

    drift = _drift(model, entry)
    if drift is not None and drift > review_threshold:
        return ModelPriceUpdate(
            model_id=model.id,
            model_key=model.model_key,
            status=PriceSyncStatus.REVIEW_NEEDED,
            message=(
                f"官方价较上次变动 {drift:.0%}，超过 {review_threshold:.0%} 复核阈值，"
                f"未自动写入"
            ),
            list_input_cost_per_million=entry.input_per_million,
            list_output_cost_per_million=entry.output_per_million,
            list_cached_cost_per_million=entry.cached_per_million,
            list_cache_write_cost_per_million=entry.cache_write_per_million,
        )

    charged = entry.discounted(model.effective_discount_percent)
    return ModelPriceUpdate(
        model_id=model.id,
        model_key=model.model_key,
        status=PriceSyncStatus.OK,
        writes=True,
        message=None,
        input_cost_per_million=charged.input_per_million,
        output_cost_per_million=charged.output_per_million,
        cached_cost_per_million=charged.cached_per_million,
        cache_write_cost_per_million=charged.cache_write_per_million,
        list_input_cost_per_million=entry.input_per_million,
        list_output_cost_per_million=entry.output_per_million,
        list_cached_cost_per_million=entry.cached_per_million,
        list_cache_write_cost_per_million=entry.cache_write_per_million,
    )


def _drift(model: ManagedModel, entry: CatalogEntry) -> float | None:
    """How far the list price moved since the last sync, or None on the first one.

    Compared against the stored list price rather than the charged rate, so changing a discount
    never looks like a suspicious price move.
    """
    previous = model.list_input_cost_per_million
    current = entry.input_per_million
    if previous is None or current is None or previous <= 0:
        return None
    return abs(current - previous) / previous
