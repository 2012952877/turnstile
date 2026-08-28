from __future__ import annotations

import json
from datetime import UTC, datetime

from backend.ingestion.copilot_cli_usage import (
    CopilotAttribution,
    merge_sources,
    parse_log,
    parse_session_store_row,
    summarize,
    to_token_usage,
)

# A faithful slice of ~/.copilot/logs/process-*.log: the request-start marker, the pretty
# printed chat.completion envelope, GitHub's metering block, and the line that closes the turn.
LOG = """2026-07-15T03:08:38.359Z [INFO] --- Start of group: Sending request to the AI model ---
2026-07-15T03:08:38.361Z [DEBUG] Applied model capabilities override: {"family":"gpt-5.4"}
2026-07-15T03:08:42.000Z [DEBUG] response (Request-ID 00000-55795bcb-9e82-444a-919c-f68bfdcc8241):
{
  "created": 1784084922,
  "model": "gpt-5.4-2026-03-05",
  "object": "chat.completion",
  "usage": {
    "completion_tokens": 139,
    "prompt_tokens": 21696,
    "total_tokens": 21835,
    "prompt_tokens_details": {
      "cached_tokens": 0
    }
  },
  "copilot_usage": {
    "token_details": [
      {
        "batch_size": 1000000,
        "cost_per_batch": 250000000000,
        "token_count": 21696,
        "token_type": "input"
      },
      {
        "batch_size": 1000000,
        "cost_per_batch": 25000000000,
        "token_count": 4000,
        "token_type": "cache_read"
      },
      {
        "batch_size": 1000000,
        "cost_per_batch": 0,
        "token_count": 1000,
        "token_type": "cache_write"
      },
      {
        "batch_size": 1000000,
        "cost_per_batch": 1500000000000,
        "token_count": 139,
        "token_type": "output"
      }
    ],
    "total_nano_aiu": 5732500000
  }
}
2026-07-15T03:08:42.063Z [INFO] --- End of group ---
"""


def test_github_reported_totals_are_recomputed_rather_than_trusted() -> None:
    """The block is only credible because its own arithmetic checks out.

    GitHub publishes both the per-bucket counts and a total; if they disagree, something about
    the parse or the format is wrong and the row must say so instead of presenting a number
    nobody verified.
    """
    (record,) = parse_log(LOG, "process-1.log")
    assert record.ingest_error is None

    broken = LOG.replace('"total_nano_aiu": 5732500000', '"total_nano_aiu": 999')
    (flagged,) = parse_log(broken, "process-1.log")
    assert flagged.ingest_error == "copilot_usage_total_mismatch"
    # Still ingested: dropping it would silently understate real spend.
    assert flagged.tokens["input"] == 21696


def test_unit_prices_come_from_github_not_from_our_registry() -> None:
    """Each request carries the rate GitHub charged it, which is what the row snapshots.

    At 1 AIU = $0.01 these are 2.50 / 0.25 / 0.00 / 15.00 per million, matching the registry's
    gpt-5.4 row on all four values including the absent cache-write meter.
    """
    (record,) = parse_log(LOG, "process-1.log")
    assert record.prices == {
        "input": 2.5,
        "cache_read": 0.25,
        "cache_write": 0.0,
        "output": 15.0,
    }
    # 21696*2.50 + 4000*0.25 + 1000*0 + 139*15.00, per million.
    assert round(record.cost_usd, 8) == 0.0573250


def test_cache_buckets_follow_the_schema_split() -> None:
    """cached_tokens is the full bucket and cache_write_tokens is its subset.

    Migration 012 constrains cache_write_tokens <= cached_tokens, and every aggregate from
    migration 001 assumes cached_tokens holds reads plus writes.
    """
    (record,) = parse_log(LOG, "process-1.log")
    assert record.tokens["cache_read"] == 4000
    assert record.tokens["cache_write"] == 1000
    assert record.cached_tokens == 5000

    usage = to_token_usage(record, CopilotAttribution())
    assert usage.usage_domain == "github_copilot"
    assert usage.cached_tokens == 5000
    assert usage.cache_write_tokens == 1000
    assert usage.cache_write_tokens <= usage.cached_tokens


def test_input_is_stored_exclusive_of_the_cache_bucket() -> None:
    """GitHub already reports input excluding cached tokens, so nothing is subtracted twice.

    Measured on 151 real records: input == prompt_tokens - cached_tokens throughout. The
    OpenAI shape in AGENTS.md section 3c needs normalizing; this one does not, and applying it
    anyway would understate input.
    """
    (record,) = parse_log(LOG, "process-1.log")
    usage = to_token_usage(record, CopilotAttribution())
    assert usage.input_tokens == 21696
    assert usage.input_tokens + usage.cached_tokens + usage.output_tokens == 26835


def test_one_model_identity_rather_than_a_dated_snapshot() -> None:
    """The response carries 'gpt-5.4-2026-03-05'; storing that splits one model in every
    GROUP BY, which is the defect migration 013 had to repair. The logged family wins."""
    (record,) = parse_log(LOG, "process-1.log")
    assert record.model == "gpt-5.4"


def test_latency_is_measured_from_the_log_not_defaulted_to_zero() -> None:
    """A fabricated zero would drag the latency KPI down and hide slow calls from governance."""
    (record,) = parse_log(LOG, "process-1.log")
    assert record.latency_ms == 3704

    without_start = LOG.replace("--- Start of group: Sending request to the AI model ---", "x")
    (unmeasured,) = parse_log(without_start, "process-1.log")
    assert unmeasured.latency_ms == 0


def test_record_ids_are_stable_so_recollection_cannot_double_count() -> None:
    """Re-running the collector re-reads the same append-only logs.

    The repository writes with ON CONFLICT (id) DO NOTHING, so a deterministic id is what makes
    that idempotent; a random one would inflate every total on the second run.
    """
    first = parse_log(LOG, "process-1.log")
    again = parse_log(LOG, "process-1.log")
    assert [item.record_id for item in first] == [item.record_id for item in again]
    other_file = parse_log(LOG, "process-2.log")
    assert other_file[0].record_id != first[0].record_id


def test_attribution_is_never_invented() -> None:
    """The CLI logs carry no enterprise scope, so the collector must not fill one in.

    A fabricated department would consume a real budget and appear in governance as fact.
    """
    (record,) = parse_log(LOG, "process-1.log")
    default = to_token_usage(record, CopilotAttribution())
    assert default.department_id == "unattributed"
    assert default.user_id == "unattributed"
    assert default.organization_id == "unattributed"
    # The runtime, though, is a real registry entity and must match it exactly so the
    # dashboard's channel switcher can resolve it.
    assert default.runtime == "GitHub Copilot CLI"
    assert default.ingest_source == "copilot_cli"
    assert default.estimated is False

    declared = to_token_usage(
        record,
        CopilotAttribution(department="AI Platform", department_id="department-platform"),
    )
    assert declared.department_id == "department-platform"


def test_records_carry_the_response_timestamp() -> None:
    (record,) = parse_log(LOG, "process-1.log")
    assert record.ts == datetime(2026, 7, 15, 3, 8, 42, tzinfo=UTC)


def test_summary_reports_what_was_measured() -> None:
    summary = summarize(parse_log(LOG, "process-1.log"))
    assert summary["requests"] == 1
    assert summary["mismatched"] == 0
    assert summary["models"] == ["gpt-5.4"]
    assert json.dumps(summary)


def test_a_log_without_usage_yields_nothing() -> None:
    assert parse_log("2026-07-15T03:08:38.359Z [INFO] nothing here\n", "process-1.log") == []


# One row of the CLI's own ledger. Note the deliberate trap: the row's input_tokens column
# counts the cached subset again, while token_details_json carries the exclusive form.
STORE_ROW: dict[str, object] = {
    "id": 42,
    "session_id": "session-abc",
    "turn_index": 0,
    "model": "gpt-5.4",
    "input_tokens": 40786,
    "cache_read_tokens": 38400,
    "cache_write_tokens": 0,
    "output_tokens": 722,
    "reasoning_tokens": 128,
    # 2386*250k + 38400*25k + 722*1.5M, i.e. GitHub's own total for these buckets.
    "total_nano_aiu": 2_639_500_000,
    "duration_ms": 8321,
    "created_at": "2026-07-16T05:12:33.100Z",
    "token_details_json": json.dumps(
        [
            {"tokenType": t, "tokenCount": n, "costPerBatch": rate, "batchSize": 1_000_000}
            for t, n, rate in (
                ("input", 2386, 250_000_000_000),
                ("cache_read", 38400, 25_000_000_000),
                ("cache_write", 0, 0),
                ("output", 722, 1_500_000_000_000),
            )
        ]
    ),
}


def test_session_store_input_comes_from_the_details_not_the_inclusive_column() -> None:
    """The row's own input column double-counts the cache; the detail block does not.

    Reading the column would bill 38,400 cached tokens at the full input rate *and* again at
    the cache rate -- the same defect AGENTS.md section 3c records for the OpenAI-via-APIM
    path. Measured on real rows: column input 40,786 against detail input 2,386.
    """
    record = parse_session_store_row(STORE_ROW)
    assert record is not None
    assert record.tokens["input"] == 2386
    assert record.tokens["cache_read"] == 38400
    assert record.tokens["input"] != STORE_ROW["input_tokens"]
    # GitHub's own total still reconciles from the buckets it published.
    assert record.ingest_error is None


def test_session_store_latency_is_measured_not_inferred() -> None:
    record = parse_session_store_row(STORE_ROW)
    assert record is not None
    assert record.latency_ms == 8321


def test_session_store_keeps_the_conversation_grouping() -> None:
    record = parse_session_store_row(STORE_ROW)
    assert record is not None
    assert record.run_id == "session-abc"
    # The store counts turns from zero; the usage schema requires one-based.
    assert record.turn_index == 1
    usage = to_token_usage(record, CopilotAttribution())
    assert usage.run_id == "session-abc"
    assert usage.turn_index == 1


def test_session_store_rows_are_keyed_so_recollection_cannot_double_count() -> None:
    first = parse_session_store_row(STORE_ROW)
    second = parse_session_store_row(dict(STORE_ROW))
    assert first is not None and second is not None
    assert first.record_id == second.record_id
    # And cannot collide with a log-derived id for a different request.
    assert first.record_id != parse_log(LOG, "process-1.log")[0].record_id


def test_the_store_wins_where_both_saw_the_same_window() -> None:
    """Two overlapping sources with no join key are separated by time, not deduplicated.

    The store began recording at a definite instant and agrees with the logs hour for hour
    from then on, so anything at or after that instant must come from exactly one of them.
    Taking both would double the overlap; taking only the store would silently drop the
    requests that happened before it existed.
    """
    store = [record for record in [parse_session_store_row(STORE_ROW)] if record is not None]
    boundary = store[0].ts

    older = parse_log(LOG, "before.log")[0]
    assert older.ts < boundary
    newer = parse_log(LOG.replace('"created": 1784084922', '"created": 1784200000'), "after.log")[0]
    assert newer.ts > boundary

    merged = merge_sources(store, [older, newer])
    assert [item.origin for item in merged] == ["log", "session-store"]
    assert newer not in merged


def test_without_a_store_the_logs_still_carry_everything() -> None:
    logs = parse_log(LOG, "process-1.log")
    assert merge_sources([], logs) == logs


def test_summary_separates_the_two_sources() -> None:
    store = [record for record in [parse_session_store_row(STORE_ROW)] if record is not None]
    summary = summarize(merge_sources(store, parse_log(LOG, "process-1.log")))
    assert summary["by_source"] == {"session-store": 1, "log-backfill": 1}
