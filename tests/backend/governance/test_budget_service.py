from datetime import UTC, date, datetime

from backend.services.budget_service import TokenBudgetService
from turnstile_core.domain.models import (
    BudgetScopeType,
    TokenBudgetWrite,
    TokenUsageRecord,
)
from turnstile_core.persistence.in_memory import InMemoryRepository


def _write_usage(
    repository: InMemoryRepository,
    user_id: str,
    sequence: int,
) -> None:
    repository.write_token_usage(
        TokenUsageRecord(
            id=f"budget-risk-{sequence}",
            request_id=f"budget-risk-{sequence}",
            correlation_id=f"budget-risk-{sequence}",
            ts=datetime(2026, 7, 20, tzinfo=UTC),
            team="AI Platform",
            organization="Contoso Global",
            organization_id="org-contoso-global",
            department="AI Platform",
            department_id="department-platform",
            project="Model FinOps",
            project_id="project-finops",
            user=user_id,
            user_id=user_id,
            agent="Delivery Engineer",
            agent_id="agent-delivery",
            workflow="budget-risk-test",
            run_id=f"budget-risk-{sequence}",
            turn_index=1,
            provider="test",
            model="test",
            model_id="test",
            runtime="test",
            request_source="test",
            input_tokens=300,
            cached_tokens=50,
            output_tokens=150,
            et=500,
            et_coeff_m=1,
            latency_ms=1,
            status="success",
            status_code=200,
            estimated_cost=0,
            estimated=False,
            ingest_source="gateway",
        )
    )


def test_compact_budget_overview_includes_people_in_risk_summary() -> None:
    repository = InMemoryRepository()
    service = TokenBudgetService(repository)
    user_id = "test.user01@contoso.com"
    _write_usage(repository, user_id, 1)

    allocations: tuple[tuple[BudgetScopeType, str, int], ...] = (
        ("organization", "org-contoso-global", 10_000),
        ("department", "department-platform", 6_000),
        ("user", user_id, 400),
    )
    for scope_type, scope_id, token_limit in allocations:
        service.save(
            "2026-07",
            scope_type,
            scope_id,
            TokenBudgetWrite(
                token_limit=token_limit,
                warning_threshold_percent=80,
            ),
            "test",
        )

    compact = service.overview("2026-07")

    assert all(item.scope_type != "user" for item in compact.items)
    assert compact.risk_count == 1
    assert compact.risk_items[0].model_dump() == {
        "scope_type": "user",
        "scope_id": user_id,
        "scope_name": user_id,
        "status": "exceeded",
        "usage_percent": 125.0,
        "forecast_percent": 125.0,
    }


def test_budget_risk_summary_is_bounded_without_losing_total_count() -> None:
    repository = InMemoryRepository()
    period_start = date(2026, 7, 1)
    for sequence in range(30):
        user_id = f"risk.user{sequence:02d}@contoso.com"
        _write_usage(repository, user_id, sequence)
        repository.upsert_token_budget(
            period_start,
            "user",
            user_id,
            "department-platform",
            400,
            80,
            "test",
        )

    compact = TokenBudgetService(repository).overview("2026-07")

    assert all(item.scope_type != "user" for item in compact.items)
    assert compact.risk_count == 30
    assert len(compact.risk_items) == 25
    assert all(item.scope_type == "user" for item in compact.risk_items)



def test_legacy_user_cache_metric_is_ignored_by_budget_surfaces() -> None:
    repository = InMemoryRepository()
    period_start = date(2026, 7, 1)
    period_end = date(2026, 8, 1)
    user_id = "lei@contoso.com"
    repository.upsert_token_budget(
        period_start,
        "user",
        user_id,
        "department-platform",
        5000,
        80,
        "owner@example.com",
    )
    _write_usage(repository, user_id, 1)
    record = repository.usage_records[-1].model_copy(
        update={
            "ts": datetime(2026, 7, 15, tzinfo=UTC),
            "input_tokens": 100,
            "cached_tokens": 0,
            "output_tokens": 0,
            "ingest_source": "eventhub",
        }
    )
    repository.usage_records[-1] = record
    bucket = record.ts.replace(minute=0, second=0, microsecond=0)
    repository.apim_cache_read_buckets[
        ("turnstile-llm", "user", user_id, bucket)
    ] = 900

    scope_rows = repository.token_usage_by_budget_scope(
        datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 8, 1, tzinfo=UTC)
    )
    ledger = repository.budget_ledger_snapshot(period_start, period_end)
    state = repository.person_budget_state(user_id, period_start, period_end)

    user_scope = next(
        row for row in scope_rows if row["scope_type"] == "user" and row["scope_id"] == user_id
    )
    assert user_scope["used_tokens"] == 100
    assert ledger[0]["confirmed_tokens"] == 100
    assert state is not None and state["used_tokens"] == 100


def test_apim_budget_surfaces_exclude_copilot_cli_usage() -> None:
    repository = InMemoryRepository()
    period_start = date(2026, 7, 1)
    period_end = date(2026, 8, 1)
    user_id = "lei@contoso.com"
    repository.upsert_token_budget(
        period_start,
        "user",
        user_id,
        "department-platform",
        5000,
        80,
        "owner@example.com",
    )
    _write_usage(repository, user_id, 1)
    apim = repository.usage_records[-1].model_copy(
        update={"input_tokens": 100, "cached_tokens": 0, "output_tokens": 0}
    )
    copilot = apim.model_copy(
        update={
            "id": "copilot-budget-usage",
            "request_id": "copilot-budget-usage",
            "correlation_id": "copilot-budget-usage",
            "input_tokens": 900,
            "runtime": "GitHub Copilot CLI",
            "ingest_source": "copilot_cli",
            "usage_domain": "github_copilot",
        }
    )
    repository.usage_records[-1] = apim
    repository.write_token_usage(copilot)

    scope_rows = repository.token_usage_by_budget_scope(
        datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 8, 1, tzinfo=UTC)
    )
    ledger = repository.budget_ledger_snapshot(period_start, period_end)
    state = repository.person_budget_state(user_id, period_start, period_end)

    user_scope = next(
        row for row in scope_rows if row["scope_type"] == "user" and row["scope_id"] == user_id
    )
    assert user_scope["used_tokens"] == 100
    assert ledger[0]["confirmed_tokens"] == 100
    assert state is not None and state["used_tokens"] == 100