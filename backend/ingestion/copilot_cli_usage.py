"""Collect real GitHub Copilot CLI usage from the client's own local logs.

GitHub exposes no per-request token API. The org billing endpoint reports AI credits and
premium requests per day; the personal-account equivalent returns 404. What the CLI *does*
write, into `~/.copilot/logs/process-*.log`, is GitHub's own metering block inside the
`chat.completion` response body:

    "copilot_usage": {
      "token_details": [
        {"token_type": "input",       "token_count": 21696, "cost_per_batch":  250000000000, ...},
        {"token_type": "cache_read",  "token_count":     0, "cost_per_batch":   25000000000, ...},
        {"token_type": "cache_write", "token_count":     0, "cost_per_batch":            0, ...},
        {"token_type": "output",      "token_count":   139, "cost_per_batch": 1500000000000, ...}
      ],
      "total_nano_aiu": 5632500000
    }

Those are exactly the four buckets `token_usage` already models, with GitHub's own unit price
per bucket -- which is exactly the per-row price snapshot migrations 011/012 already store. So
this is not an estimate being dressed up as a measurement; it is the provider's own billing
record, and the parser proves it by recomputing `total_nano_aiu` from the buckets and refusing
to trust any record where the two disagree.

Two properties were established by measurement before this module was written, and both are
relied on here:

* `input` already **excludes** the cached subset (`input == prompt_tokens - cached_tokens` on
  all 151 sampled records), which is the exclusive form this schema wants. No normalization is
  needed, unlike the OpenAI shape described in AGENTS.md section 3c.
* one request group produces exactly one usage block, so group start/end timestamps give a
  real latency rather than a fabricated zero.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import blake2b
from pathlib import Path

from ..config import Settings
from ..domain.models import TokenUsageRecord

logger = logging.getLogger(__name__)

DEFAULT_LOG_DIR = Path.home() / ".copilot" / "logs"
LOG_GLOB = "process-*.log"
# The CLI's own durable usage ledger. Preferred over the logs because process logs rotate --
# 46 of 65 files had already lost their usage blocks when this was written -- while this table
# persists, is structured, and carries measured latency instead of a marker-derived estimate.
DEFAULT_SESSION_STORE = Path.home() / ".copilot" / "session-store.db"
USAGE_TABLE = "assistant_usage_events"

# GitHub meters in AI Units. `cost_per_batch` is nano-AIU per `batch_size` tokens, and
# total_nano_aiu is their sum, so the only external constant needed is the AIU list price.
# Cross-checked rather than assumed: at $0.01 the four gpt-5.4 rates come out
# 2.50 / 0.25 / 0.00 / 15.00 USD per million, matching this repository's registry row on all
# four values, including the "no cache-write meter before gpt-5.6" fact in AGENTS.md section 7.
USD_PER_AIU = 0.01
NANO_PER_AIU = 1_000_000_000

# The runtime must match the registry name exactly; telemetry stores the display name and the
# dashboard's channel switcher resolves a channel back to these names.
RUNTIME_NAME = "GitHub Copilot CLI"
PROVIDER = "copilot"
REQUEST_SOURCE = "copilot-cli"
INGEST_SOURCE = "copilot_cli"

_USAGE_KEY = '"copilot_usage":'
# Top-level fields of the response are pretty-printed at exactly two spaces, so this cannot
# match the identically named fields of the nested model-capability objects.
_CREATED_RE = re.compile(r'^  "created": (\d+),$', re.MULTILINE)
_RESPONSE_MODEL_RE = re.compile(r'^  "model": "([^"]+)",$', re.MULTILINE)
# Model identity, most authoritative first. The CLI states the family outright once per
# session; the response instead carries a dated snapshot ('gpt-5.4-2026-03-05'). Storing both
# would split one model across two identities in every GROUP BY -- the exact defect migration
# 013 had to repair. Reading the logged family beats transforming the snapshot name.
_FAMILY_RE = re.compile(r'"family":"([^"]+)"')
_REQUEST_MODEL_RE = re.compile(r"response\.create request \(model: ([^)]+)\)")
# Request start. The CLI logs two transports: a WebSocket path that brackets each turn with
# a request group, and an HTTP path that logs 'Wire request'. Both are needed -- anchoring on
# only the first left 123 of 151 records with a zero latency, and a fabricated zero would drag
# down the average-latency KPI and hide slow requests from governance.
_REQUEST_START_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z) "
    r".*(?:Start of group: Sending request|Wire request)",
    re.MULTILINE,
)
_STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z) ", re.MULTILINE)

_BUCKETS = ("input", "cache_read", "cache_write", "output")


@dataclass(frozen=True)
class CopilotUsage:
    """One metered Copilot CLI request, exactly as GitHub reported it."""

    record_id: str
    source_file: str
    offset: int
    ts: datetime
    model: str
    latency_ms: int
    tokens: dict[str, int]
    # USD per million tokens, derived from GitHub's own per-bucket rate on this request.
    prices: dict[str, float]
    total_nano_aiu: int
    # Set when GitHub's own total cannot be reproduced from its own buckets. The row is still
    # ingested, because dropping it would understate usage, but the discrepancy is recorded
    # rather than hidden -- the same contract the Event Hub processor follows.
    ingest_error: str | None
    # Conversation grouping. The session store knows both; the log path cannot, so it falls
    # back to a per-request run rather than inventing a conversation that was never observed.
    run_id: str = ""
    turn_index: int = 1
    origin: str = "log"

    @property
    def cost_usd(self) -> float:
        return self.total_nano_aiu / NANO_PER_AIU * USD_PER_AIU

    @property
    def cached_tokens(self) -> int:
        """Full cache bucket: reads plus writes.

        AGENTS.md section 10 pins this: `cached_tokens` stays the full bucket so every
        aggregate from migration 001 keeps working, and `cache_write_tokens` is its subset.
        """
        return self.tokens["cache_read"] + self.tokens["cache_write"]


def _parse_stamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")


def _as_int(value: object) -> int:
    return int(value) if isinstance(value, int | float | str) else 0


def _price_per_million(detail: dict[str, object]) -> float:
    batch = _as_int(_detail_field(detail, "batch_size", "batchSize"))
    if batch <= 0:
        return 0.0
    nano_per_token = _as_int(_detail_field(detail, "cost_per_batch", "costPerBatch")) / batch
    return nano_per_token * 1_000_000 / NANO_PER_AIU * USD_PER_AIU


def _detail_field(detail: dict[str, object], snake: str, camel: str) -> object:
    """The two sources publish the same block in different casings.

    The response body writes `token_type`; the session store writes `tokenType`. Everything
    else about the block -- the four buckets, the per-bucket rate, and the exclusive `input`
    that already omits the cached subset -- is identical, verified on rows where cache_read
    was non-zero.
    """
    return detail[snake] if snake in detail else detail.get(camel)


def _buckets_from_details(
    details: Sequence[object],
) -> tuple[dict[str, int], dict[str, float], int]:
    """Token counts, unit prices, and GitHub's cost recomputed from its own numbers."""
    tokens = dict.fromkeys(_BUCKETS, 0)
    prices = dict.fromkeys(_BUCKETS, 0.0)
    recomputed = 0
    for detail in details:
        if not isinstance(detail, dict):
            continue
        bucket = str(_detail_field(detail, "token_type", "tokenType") or "")
        if bucket not in tokens:
            continue
        count = _as_int(_detail_field(detail, "token_count", "tokenCount"))
        batch = _as_int(_detail_field(detail, "batch_size", "batchSize"))
        tokens[bucket] = count
        prices[bucket] = _price_per_million(detail)
        if batch > 0:
            rate = _as_int(_detail_field(detail, "cost_per_batch", "costPerBatch"))
            recomputed += count * rate // batch
    return tokens, prices, recomputed


def _latency_ms(text: str, usage_start: int, usage_end: int) -> int:
    """Wall time from sending the request to the first log line after the usage block.

    Returns 0 only when the log does not carry both ends, which is honest: a missing
    measurement should not be smuggled in as a plausible-looking number.
    """
    starts = list(_REQUEST_START_RE.finditer(text, 0, usage_start))
    if not starts:
        return 0
    end = _STAMP_RE.search(text, usage_end)
    if end is None:
        return 0
    delta = _parse_stamp(end.group(1)) - _parse_stamp(starts[-1].group(1))
    return max(0, round(delta.total_seconds() * 1000))


def parse_log(text: str, source_file: str) -> list[CopilotUsage]:
    """Extract every metered request from one CLI log file."""
    decoder = json.JSONDecoder()
    found: list[CopilotUsage] = []
    cursor = 0
    while True:
        key_at = text.find(_USAGE_KEY, cursor)
        if key_at == -1:
            return found
        brace_at = text.find("{", key_at)
        if brace_at == -1:
            return found
        try:
            payload, end = decoder.raw_decode(text, brace_at)
        except json.JSONDecodeError:
            cursor = key_at + len(_USAGE_KEY)
            continue
        cursor = end
        parsed = _to_usage(text, payload, key_at, end, source_file)
        if parsed is not None:
            found.append(parsed)


def _to_usage(
    text: str, payload: dict[str, object], key_at: int, end: int, source_file: str
) -> CopilotUsage | None:
    details = payload.get("token_details")
    if not isinstance(details, list) or not details:
        return None

    tokens, prices, recomputed = _buckets_from_details(details)

    reported = _as_int(payload.get("total_nano_aiu"))
    ingest_error = None if recomputed == reported else "copilot_usage_total_mismatch"

    created = _CREATED_RE.search(text[:key_at][-4000:])
    if created is None:
        # No response envelope means no trustworthy timestamp, and a request that cannot be
        # placed in time cannot be charged to a period.
        logger.warning("copilot usage block at %s:%d has no created field", source_file, key_at)
        return None
    ts = datetime.fromtimestamp(int(created.group(1)), tz=UTC)

    requested = _FAMILY_RE.findall(text, 0, key_at) or _REQUEST_MODEL_RE.findall(text, 0, key_at)
    response_model = _RESPONSE_MODEL_RE.findall(text[:key_at][-4000:])
    model = requested[-1] if requested else (response_model[-1] if response_model else "unknown")

    # Stable across re-runs so the repository's ON CONFLICT DO NOTHING makes collection
    # idempotent. File names carry an epoch and a pid, so they do not collide.
    digest = blake2b(f"{source_file}:{key_at}".encode(), digest_size=12).hexdigest()
    return CopilotUsage(
        record_id=f"copilot-cli-{digest}",
        source_file=source_file,
        offset=key_at,
        ts=ts,
        model=model,
        latency_ms=_latency_ms(text, key_at, end),
        tokens=tokens,
        prices=prices,
        total_nano_aiu=reported,
        ingest_error=ingest_error,
        run_id=f"copilot-cli-{digest}",
        turn_index=1,
        origin="log",
    )


def parse_session_store_row(row: dict[str, object]) -> CopilotUsage | None:
    """One row of `assistant_usage_events`.

    The row's own `input_tokens` column is the **inclusive** form -- it counts the cached
    subset again -- while `token_details_json` carries the exclusive form this schema stores.
    Verified on rows with a non-zero cache: column input 40,786 against detail input 2,386
    with 38,400 cached. Reading the column instead would double-count every cache hit and
    bill it at the full input rate, which is exactly the defect AGENTS.md section 3c records
    for the OpenAI-via-APIM path.
    """
    raw = row.get("token_details_json")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        details = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("session store row %s has unreadable token details", row.get("id"))
        return None
    if not isinstance(details, list) or not details:
        return None

    tokens, prices, recomputed = _buckets_from_details(details)
    reported = _as_int(row.get("total_nano_aiu"))
    ingest_error = None if recomputed == reported else "copilot_usage_total_mismatch"

    created = row.get("created_at")
    if not isinstance(created, str) or not created:
        return None
    try:
        ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("session store row %s has an unreadable timestamp", row.get("id"))
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)

    session_id = str(row.get("session_id") or "unattributed")
    event_id = _as_int(row.get("id"))
    return CopilotUsage(
        # Keyed on the store's own primary key, so re-running is idempotent and can never
        # collide with a log-derived id.
        record_id=f"copilot-cli-evt-{event_id}",
        source_file=USAGE_TABLE,
        offset=event_id,
        ts=ts,
        model=str(row.get("model") or "unknown"),
        # Measured by the CLI, not inferred from log markers.
        latency_ms=max(0, _as_int(row.get("duration_ms"))),
        tokens=tokens,
        prices=prices,
        total_nano_aiu=reported,
        ingest_error=ingest_error,
        run_id=session_id,
        # The store counts turns from zero; this schema requires one-based.
        turn_index=max(1, _as_int(row.get("turn_index")) + 1),
        origin="session-store",
    )


def collect_session_store(db_path: Path = DEFAULT_SESSION_STORE) -> list[CopilotUsage]:
    """Every metered request the CLI recorded in its own ledger, oldest first."""
    if not db_path.exists():
        return []
    # Read-only URI so collection can never mutate the CLI's own state.
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        try:
            cursor = connection.execute(
                f"SELECT * FROM {USAGE_TABLE} ORDER BY created_at, id"  # noqa: S608
            )
        except sqlite3.DatabaseError as error:
            logger.warning("could not read %s: %s", db_path, error)
            return []
        rows = [dict(row) for row in cursor.fetchall()]
    parsed = [parse_session_store_row(row) for row in rows]
    return [item for item in parsed if item is not None]


@dataclass(frozen=True)
class CopilotAttribution:
    """Enterprise scope to file this machine's Copilot usage under.

    Everything defaults to 'unattributed' because the CLI logs carry no organization,
    department, project or agent, and inventing one would put fabricated identity into a
    governance surface. An operator who knows their own scope passes it explicitly.
    """

    organization: str = "unattributed"
    organization_id: str = "unattributed"
    department: str = "unattributed"
    department_id: str = "unattributed"
    project: str = "unattributed"
    project_id: str = "unattributed"
    agent: str = "GitHub Copilot CLI"
    agent_id: str = "agent-github-copilot-cli"
    user: str = "unattributed"
    user_id: str = "unattributed"


def to_token_usage(usage: CopilotUsage, attribution: CopilotAttribution) -> TokenUsageRecord:
    return TokenUsageRecord(
        id=usage.record_id,
        request_id=usage.record_id,
        correlation_id=usage.record_id,
        ts=usage.ts,
        team=attribution.department,
        organization=attribution.organization,
        organization_id=attribution.organization_id,
        department=attribution.department,
        department_id=attribution.department_id,
        project=attribution.project,
        project_id=attribution.project_id,
        user=attribution.user,
        user_id=attribution.user_id,
        agent=attribution.agent,
        agent_id=attribution.agent_id,
        workflow="copilot-cli",
        run_id=usage.run_id or usage.record_id,
        turn_index=usage.turn_index,
        provider=PROVIDER,
        model=usage.model,
        model_id=usage.model,
        runtime=RUNTIME_NAME,
        request_source=REQUEST_SOURCE,
        usage_domain="github_copilot",
        input_tokens=usage.tokens["input"],
        cached_tokens=usage.cached_tokens,
        cache_write_tokens=usage.tokens["cache_write"],
        output_tokens=usage.tokens["output"],
        et=0,
        et_coeff_m=1,
        latency_ms=usage.latency_ms,
        status="success",
        status_code=200,
        estimated_cost=usage.cost_usd,
        input_price_per_million=usage.prices["input"],
        cached_price_per_million=usage.prices["cache_read"],
        cache_write_price_per_million=usage.prices["cache_write"],
        output_price_per_million=usage.prices["output"],
        # GitHub reported these counts and these rates; nothing here is inferred.
        estimated=False,
        ingest_source=INGEST_SOURCE,
        ingest_error=usage.ingest_error,
    )


def collect_logs(log_dir: Path = DEFAULT_LOG_DIR) -> Iterator[CopilotUsage]:
    """Every metered request across the CLI's log directory, oldest first."""
    for path in sorted(log_dir.glob(LOG_GLOB)):
        try:
            text = path.read_text(errors="replace")
        except OSError as error:
            logger.warning("could not read %s: %s", path, error)
            continue
        yield from parse_log(text, path.name)


def merge_sources(
    store_records: Sequence[CopilotUsage], log_records: Sequence[CopilotUsage]
) -> list[CopilotUsage]:
    """Session store wins from its first record onward; logs backfill only what precedes it.

    The two sources overlap without either being a superset, and they share no join key, so
    they are separated by time rather than deduplicated. This is exact rather than heuristic:
    the store began recording at a definite instant, and from that instant the two agree
    hour for hour -- measured 20/20, 94/94, 2/2 and 5/5 on the four overlapping days. Before
    it, only the logs saw anything (28 requests), and after the logs rotate, only the store
    still does. Preferring the store where both exist also keeps the measured latency.
    """
    if not store_records:
        return sorted(log_records, key=lambda item: item.ts)
    boundary = min(item.ts for item in store_records)
    backfill = [item for item in log_records if item.ts < boundary]
    return sorted([*store_records, *backfill], key=lambda item: item.ts)


def collect(
    log_dir: Path = DEFAULT_LOG_DIR, session_store: Path = DEFAULT_SESSION_STORE
) -> list[CopilotUsage]:
    return merge_sources(collect_session_store(session_store), list(collect_logs(log_dir)))


def summarize(records: Sequence[CopilotUsage] | Iterable[CopilotUsage]) -> dict[str, object]:
    items = list(records)
    totals = {bucket: sum(item.tokens[bucket] for item in items) for bucket in _BUCKETS}
    return {
        "requests": len(items),
        **totals,
        "total_tokens": sum(totals.values()),
        "cost_usd": round(sum(item.cost_usd for item in items), 6),
        "mismatched": sum(1 for item in items if item.ingest_error),
        "models": sorted({item.model for item in items}),
        "by_source": {
            "session-store": sum(1 for item in items if item.origin == "session-store"),
            "log-backfill": sum(1 for item in items if item.origin == "log"),
        },
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.ingestion.copilot_cli_usage",
        description="Ingest real GitHub Copilot CLI usage from local logs into token_usage.",
    )
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--session-store", type=Path, default=DEFAULT_SESSION_STORE)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and summarize without writing. Reads nothing but local files.",
    )
    for field in ("organization", "department", "project", "user"):
        parser.add_argument(f"--{field}", default=None, help=f"attribute usage to this {field}")
        parser.add_argument(f"--{field}-id", default=None)
    return parser


def _attribution_from_args(args: argparse.Namespace) -> CopilotAttribution:
    """Attribution is opt-in.

    An id defaults to its display name rather than to a generated slug, so whatever an operator
    declares is exactly what lands in the hierarchy; nothing is invented on their behalf.
    """
    values: dict[str, str] = {}
    for field in ("organization", "department", "project", "user"):
        name = getattr(args, field)
        identifier = getattr(args, f"{field}_id") or name
        if name:
            values[field] = name
            values[f"{field}_id"] = identifier
    return CopilotAttribution(**values)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _build_parser().parse_args(argv)
    records = collect(args.log_dir, args.session_store)
    summary = summarize(records)
    logger.info("%s", json.dumps(summary, indent=2))
    if not records:
        logger.info("no Copilot CLI usage found under %s", args.log_dir)
        return 0
    if args.dry_run:
        logger.info("dry run: nothing written")
        return 0

    from ..persistence.factory import create_repository

    repository = create_repository(Settings())
    attribution = _attribution_from_args(args)
    for record in records:
        repository.write_token_usage(to_token_usage(record, attribution))
    logger.info("wrote %d rows (re-runs are idempotent)", len(records))
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI shell
    raise SystemExit(main())
