from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol, cast
from uuid import UUID

import httpx

from .contracts import (
    CopilotAiCreditItem,
    CopilotBreakdownItem,
    CopilotBudgetRequest,
    CopilotBudgetSummary,
    CopilotCostCenterRequest,
    CopilotDailyUsage,
    CopilotGovernanceMember,
    CopilotMemberUsage,
    CopilotUsageImportKind,
    CopilotUsageImportSummary,
    CopilotUsageTotals,
)


class CopilotNotConfiguredError(ValueError):
    pass


class CopilotIdentityRequiredError(ValueError):
    pass


class CopilotNotFoundError(ValueError):
    pass


class CopilotPermissionError(ValueError):
    pass


class CopilotStoreProtocol(Protocol):
    def list_connections(self) -> Sequence[dict[str, Any]]: ...

    def connection(self, organization: str | None = None) -> dict[str, Any] | None: ...

    def save_connection(
        self,
        *,
        organization: str,
        display_name: str,
        credential_ciphertext: bytes,
        credential_hint: str,
        write_enabled: bool,
        set_default: bool,
        updated_by: str,
    ) -> dict[str, Any]: ...

    def identity(self, app_user_id: UUID) -> dict[str, Any] | None: ...

    def list_identities(self) -> Sequence[dict[str, Any]]: ...

    def save_identity(
        self, app_user_id: UUID, github_login: str, updated_by: str
    ) -> dict[str, Any]: ...

    def oauth_setting(self, origin: str) -> dict[str, Any] | None: ...

    def save_oauth_setting(
        self,
        *,
        origin: str,
        client_id: str,
        client_secret_ciphertext: bytes,
        client_secret_hint: str,
        callback_url: str | None,
        updated_by: str,
    ) -> dict[str, Any]: ...

    def create_oauth_state(
        self,
        state_sha256: str,
        app_user_id: UUID,
        origin: str,
        return_origin: str,
        return_path: str,
        purpose: str,
        organization: str | None,
        expires_at: datetime,
    ) -> None: ...

    def consume_oauth_state(
        self, state_sha256: str, origin: str
    ) -> dict[str, Any] | None: ...

    def create_budget_request(
        self,
        *,
        connection_id: UUID,
        organization: str,
        app_user_id: UUID,
        user_email: str,
        user_display_name: str | None,
        github_login: str,
        requested_amount_usd: int,
        reason: str,
    ) -> dict[str, Any]: ...

    def list_budget_requests(
        self, app_user_id: UUID | None = None
    ) -> Sequence[dict[str, Any]]: ...

    def budget_request(self, request_id: UUID) -> dict[str, Any] | None: ...

    def claim_budget_review(self, request_id: UUID, actor: str) -> dict[str, Any] | None: ...

    def complete_budget_review(
        self,
        *,
        request_id: UUID,
        actor: str,
        status: str,
        approved_amount_usd: int | None,
        review_comment: str,
        github_sync_status: str,
        github_sync_error: str | None,
    ) -> dict[str, Any]: ...

    def save_usage_import(
        self,
        *,
        connection_id: UUID,
        source_kind: str,
        filename: str,
        content_sha256: str,
        file_size_bytes: int,
        rows: Sequence[dict[str, Any]],
        first_usage_date: object,
        last_usage_date: object,
        uploaded_by: UUID,
        uploaded_by_email: str,
    ) -> dict[str, Any]: ...

    def usage_imports(
        self, connection_id: UUID, source_kind: str
    ) -> Sequence[dict[str, Any]]: ...

    def imported_usage(
        self, connection_id: UUID, source_kind: str
    ) -> dict[str, Any]: ...

    def create_cost_center_request(self, **values: Any) -> dict[str, Any]: ...

    def list_cost_center_requests(
        self, app_user_id: UUID | None = None
    ) -> Sequence[dict[str, Any]]: ...

    def cost_center_request(self, request_id: UUID) -> dict[str, Any] | None: ...

    def claim_cost_center_review(
        self, request_id: UUID, actor: str
    ) -> dict[str, Any] | None: ...

    def complete_cost_center_review(
        self,
        *,
        request_id: UUID,
        actor: str,
        status: str,
        review_comment: str,
        github_sync_status: str,
        github_sync_error: str | None,
    ) -> dict[str, Any]: ...


class CopilotApiProtocol(Protocol):
    async def aclose(self) -> None: ...

    async def profile(self) -> dict[str, Any]: ...

    async def organization(self, organization: str) -> dict[str, Any]: ...

    async def billing(self, organization: str) -> dict[str, Any]: ...

    async def seats(self, organization: str) -> list[dict[str, Any]]: ...

    async def member_seat(self, organization: str, login: str) -> dict[str, Any]: ...

    async def usage_report(
        self, organization: str, kind: Literal["organization", "users"]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]: ...

    async def ai_credit_usage(
        self,
        organization: str,
        year: int,
        month: int,
        *,
        user: str | None = None,
    ) -> dict[str, Any]: ...

    async def billing_usage(
        self,
        organization: str,
        year: int,
        month: int,
    ) -> dict[str, Any]: ...

    async def budgets(
        self, organization: str, *, user: str | None = None
    ) -> list[dict[str, Any]]: ...

    async def all_budgets(self, enterprise: str) -> list[dict[str, Any]]: ...

    async def enterprise_cost_centers(
        self, enterprise: str
    ) -> list[dict[str, Any]]: ...

    async def enterprise_teams(self, enterprise: str) -> list[dict[str, Any]]: ...

    async def enterprise_team_memberships(
        self, enterprise: str, team_slug: str
    ) -> list[dict[str, Any]]: ...

    async def enterprise_team_organizations(
        self, enterprise: str, team_slug: str
    ) -> list[dict[str, Any]]: ...

    async def organization_members(
        self, organization: str
    ) -> list[dict[str, Any]]: ...

    async def organization_team_members(
        self, organization: str, team_slug: str
    ) -> list[dict[str, Any]]: ...

    async def add_cost_center_users(
        self, enterprise: str, cost_center_id: str, users: Sequence[str]
    ) -> dict[str, Any]: ...

    async def create_user_budget(
        self, organization: str, login: str, amount_usd: int
    ) -> dict[str, Any]: ...

    async def update_user_budget(
        self, organization: str, budget_id: str, amount_usd: int
    ) -> dict[str, Any]: ...


CopilotApiFactory = Callable[[str], CopilotApiProtocol]
OAuthClientFactory = Callable[[], httpx.AsyncClient]

PLAN_PRICES_USD = {"business": 19.0, "enterprise": 39.0}


def _number(value: object) -> float:
    return float(value) if isinstance(value, int | float | Decimal) else 0.0


def _integer(value: object) -> int:
    return max(0, int(_number(value)))


def _date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _daily_rows(records: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for record in records:
        values = record.get("day_totals")
        if isinstance(values, list):
            rows.extend(
                cast(Mapping[str, Any], value)
                for value in values
                if isinstance(value, dict)
            )
        elif _date(record.get("day")) is not None:
            rows.append(record)
    return rows


def _usage_totals(
    records: Sequence[Mapping[str, Any]],
    *,
    seats: int = 0,
    gross_amount: float = 0,
    net_amount: float = 0,
    ai_credits_used: float | None = None,
) -> CopilotUsageTotals:
    interactions = sum(
        _integer(record.get("user_initiated_interaction_count")) for record in records
    )
    generations = sum(_integer(record.get("code_generation_activity_count")) for record in records)
    acceptances = sum(_integer(record.get("code_acceptance_activity_count")) for record in records)
    user_logins = {
        str(record.get("user_login", "")).lower()
        for record in records
        if str(record.get("user_login", ""))
    }
    monthly_active = max(
        (_integer(record.get("monthly_active_users")) for record in records), default=0
    )
    cli_prompt_tokens = 0
    cli_output_tokens = 0
    for record in records:
        cli = record.get("totals_by_cli")
        token_usage = cli.get("token_usage") if isinstance(cli, Mapping) else None
        if isinstance(token_usage, Mapping):
            cli_prompt_tokens += _integer(token_usage.get("prompt_tokens_sum"))
            cli_output_tokens += _integer(token_usage.get("output_tokens_sum"))
    active_users = monthly_active or sum(
        1
        for login in user_logins
        if any(
            str(record.get("user_login", "")).lower() == login
            and (
                _integer(record.get("user_initiated_interaction_count"))
                + _integer(record.get("code_generation_activity_count"))
                + _integer(record.get("code_acceptance_activity_count"))
                + _integer(record.get("loc_added_sum"))
                > 0
            )
            for record in records
        )
    )
    return CopilotUsageTotals(
        seats=seats,
        active_users=active_users,
        interactions=interactions,
        generations=generations,
        acceptances=acceptances,
        acceptance_rate=round(100 * acceptances / generations, 1) if generations else 0,
        loc_added=sum(_integer(record.get("loc_added_sum")) for record in records),
        loc_deleted=sum(_integer(record.get("loc_deleted_sum")) for record in records),
        ai_credits_used=round(
            ai_credits_used
            if ai_credits_used is not None
            else sum(_number(record.get("ai_credits_used")) for record in records),
            4,
        ),
        gross_amount=round(gross_amount, 4),
        net_amount=round(net_amount, 4),
        cli_prompt_tokens=cli_prompt_tokens,
        cli_output_tokens=cli_output_tokens,
    )


def _breakdown(
    records: Sequence[Mapping[str, Any]], field: str, key_field: str
) -> list[CopilotBreakdownItem]:
    grouped: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "interactions": 0,
            "generations": 0,
            "acceptances": 0,
            "loc_added": 0,
            "loc_deleted": 0,
        }
    )
    for record in records:
        values = record.get(field)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, Mapping):
                continue
            key = str(item.get(key_field, "")).strip()
            if not key:
                continue
            totals = grouped[key]
            totals["interactions"] += _integer(item.get("user_initiated_interaction_count"))
            totals["generations"] += _integer(item.get("code_generation_activity_count"))
            totals["acceptances"] += _integer(item.get("code_acceptance_activity_count"))
            totals["loc_added"] += _integer(item.get("loc_added_sum"))
            totals["loc_deleted"] += _integer(item.get("loc_deleted_sum"))
    return [
        CopilotBreakdownItem(key=key, label=key, **values)
        for key, values in sorted(
            grouped.items(),
            key=lambda entry: (
                -entry[1]["interactions"],
                -entry[1]["generations"],
                -entry[1]["loc_added"],
                entry[0],
            ),
        )
    ]


def _ai_credit_breakdown(
    records: Sequence[Mapping[str, Any]],
) -> list[CopilotAiCreditItem]:
    grouped: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "gross_quantity": 0,
            "discount_quantity": 0,
            "net_quantity": 0,
            "net_cost": 0,
        }
    )
    for record in records:
        model = str(
            record.get("model")
            or record.get("sku")
            or record.get("product")
            or "Unknown"
        ).strip()
        values = grouped[model]
        gross = _number(record.get("grossQuantity"))
        net = _number(record.get("netQuantity"))
        discount = _number(record.get("discountQuantity"))
        if discount == 0 and gross >= net:
            discount = gross - net
        values["gross_quantity"] += gross
        values["discount_quantity"] += discount
        values["net_quantity"] += net
        values["net_cost"] += _number(record.get("netAmount"))
    return [
        CopilotAiCreditItem(model=model, **values)
        for model, values in sorted(
            grouped.items(),
            key=lambda entry: (-entry[1]["gross_quantity"], -entry[1]["net_cost"], entry[0]),
        )
    ]


def _daily_usage(
    records: Sequence[Mapping[str, Any]], user_records: Sequence[Mapping[str, Any]]
) -> list[CopilotDailyUsage]:
    user_credits: dict[date, float] = defaultdict(float)
    for record in user_records:
        day = _date(record.get("day"))
        if day is not None:
            user_credits[day] += _number(record.get("ai_credits_used"))
    grouped: dict[date, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        day = _date(record.get("day"))
        if day is not None:
            grouped[day].append(record)

    feature_users: dict[date, dict[str, set[str]]] = defaultdict(
        lambda: {"agent": set(), "chat": set()}
    )
    for record in user_records:
        day = _date(record.get("day"))
        login = str(record.get("user_login") or "").strip().lower()
        if day is None or not login:
            continue
        features: list[Mapping[str, Any]] = []
        for field in ("totals_by_feature", "totals_by_model_feature"):
            values = record.get(field)
            if isinstance(values, list):
                features.extend(value for value in values if isinstance(value, Mapping))
        for feature in features:
            name = str(feature.get("feature") or "").strip().lower()
            activity = sum(
                _integer(feature.get(field))
                for field in (
                    "user_initiated_interaction_count",
                    "code_generation_activity_count",
                    "code_acceptance_activity_count",
                    "loc_added_sum",
                    "loc_deleted_sum",
                )
            )
            if not name or activity <= 0:
                continue
            if "agent" in name or name in {"copilot_cli", "agent_edit"}:
                feature_users[day]["agent"].add(login)
            if "chat" in name or name == "copilot_app":
                feature_users[day]["chat"].add(login)
    return [
        CopilotDailyUsage(
            day=day,
            active_users=max(
                (_integer(record.get("daily_active_users")) for record in values),
                default=0,
            )
            or len({str(record.get("user_login", "")).lower() for record in values}),
            weekly_active_users=max(
                (_integer(record.get("weekly_active_users")) for record in values),
                default=0,
            ),
            monthly_active_users=max(
                (_integer(record.get("monthly_active_users")) for record in values),
                default=0,
            ),
            agent_users=len(feature_users[day]["agent"]),
            chat_users=len(feature_users[day]["chat"]),
            interactions=sum(
                _integer(record.get("user_initiated_interaction_count")) for record in values
            ),
            generations=sum(
                _integer(record.get("code_generation_activity_count")) for record in values
            ),
            acceptances=sum(
                _integer(record.get("code_acceptance_activity_count")) for record in values
            ),
            loc_added=sum(_integer(record.get("loc_added_sum")) for record in values),
            loc_deleted=sum(_integer(record.get("loc_deleted_sum")) for record in values),
            ai_credits_used=round(user_credits[day], 4),
        )
        for day, values in sorted(grouped.items())
    ]


def _budget_for_login(
    budgets: Sequence[Mapping[str, Any]], login: str
) -> Mapping[str, Any] | None:
    target = login.lower()
    for budget in budgets:
        owner = str(budget.get("user") or budget.get("budget_entity_name") or "").lower()
        if budget.get("budget_scope") == "user" and owner == target:
            return budget
    return None


def _budget_request(row: Mapping[str, Any]) -> CopilotBudgetRequest:
    return CopilotBudgetRequest.model_validate(
        {field: row.get(field) for field in CopilotBudgetRequest.model_fields}
    )


def _member(
    seat: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    budgets: Sequence[Mapping[str, Any]],
) -> CopilotMemberUsage | None:
    assignee = seat.get("assignee")
    if not isinstance(assignee, Mapping):
        return None
    login = str(assignee.get("login", "")).strip()
    if not login:
        return None
    budget = _budget_for_login(budgets, login)
    amount = _number(budget.get("budget_amount")) if budget else None
    consumed = _number(budget.get("consumed_amount")) if budget else None
    phase: str | None = None
    for record in sorted(records, key=lambda value: str(value.get("day", "")), reverse=True):
        adoption = record.get("ai_adoption_phase")
        if isinstance(adoption, Mapping) and adoption.get("phase"):
            phase = str(adoption["phase"])
            break
    team = seat.get("assigning_team")
    return CopilotMemberUsage(
        login=login,
        avatar_url=str(assignee.get("avatar_url")) if assignee.get("avatar_url") else None,
        plan_type=str(seat.get("plan_type") or "unknown"),
        seat_created_at=_datetime(seat.get("created_at")),
        last_activity_at=_datetime(seat.get("last_activity_at")),
        last_activity_editor=(
            str(seat.get("last_activity_editor")) if seat.get("last_activity_editor") else None
        ),
        pending_cancellation_date=_date(seat.get("pending_cancellation_date")),
        assigning_team=(
            str(team.get("name") or team.get("slug"))
            if isinstance(team, Mapping) and (team.get("name") or team.get("slug"))
            else None
        ),
        adoption_phase=phase,
        budget_amount=amount,
        budget_consumed=consumed,
        budget_remaining=(
            round(amount - consumed, 4)
            if amount is not None and consumed is not None
            else None
        ),
        totals=_usage_totals(records, seats=1),
    )


def _governance_member(
    login: str,
    seats: Mapping[str, Mapping[str, Any]],
    profile: Mapping[str, Any] | None = None,
) -> CopilotGovernanceMember:
    seat = seats.get(login.lower())
    assignee = seat.get("assignee") if seat else None
    source = profile or (assignee if isinstance(assignee, Mapping) else {})
    return CopilotGovernanceMember(
        login=login,
        avatar_url=(str(source["avatar_url"]) if source.get("avatar_url") else None),
        has_seat=seat is not None,
        last_activity_at=_datetime(seat.get("last_activity_at")) if seat else None,
        last_activity_editor=(
            str(seat["last_activity_editor"])
            if seat and seat.get("last_activity_editor")
            else None
        ),
        plan_type=str(seat["plan_type"]) if seat and seat.get("plan_type") else None,
    )


def _budget_summary(raw: Mapping[str, Any]) -> CopilotBudgetSummary:
    raw_skus = raw.get("budget_product_skus")
    product_skus = (
        [str(value) for value in raw_skus if isinstance(value, str) and value]
        if isinstance(raw_skus, list)
        else []
    )
    if not product_skus and raw.get("budget_product_sku"):
        product_skus = [str(raw["budget_product_sku"])]
    amount = _number(raw.get("budget_amount"))
    consumed = (
        _number(raw.get("consumed_amount"))
        if raw.get("consumed_amount") is not None
        else None
    )
    alerting = raw.get("budget_alerting")
    alerting = alerting if isinstance(alerting, Mapping) else {}
    raw_recipients = alerting.get("alert_recipients")
    recipients = (
        [str(value) for value in raw_recipients if isinstance(value, str)]
        if isinstance(raw_recipients, list)
        else []
    )
    return CopilotBudgetSummary(
        id=str(raw.get("id") or ""),
        budget_type=str(raw.get("budget_type") or ""),
        scope=str(raw.get("budget_scope") or ""),
        entity_name=str(raw.get("budget_entity_name") or raw.get("user") or ""),
        product_skus=product_skus,
        amount=amount,
        consumed_amount=round(consumed, 4) if consumed is not None else None,
        remaining_amount=(round(amount - consumed, 4) if consumed is not None else None),
        usage_percent=(
            round(consumed / amount * 100, 1)
            if consumed is not None and amount > 0
            else None
        ),
        prevent_further_usage=bool(raw.get("prevent_further_usage")),
        will_alert=bool(alerting.get("will_alert")),
        alert_recipients=recipients,
    )


def _usage_import(row: Mapping[str, Any]) -> CopilotUsageImportSummary:
    return CopilotUsageImportSummary(
        upload_id=UUID(str(row["id"])),
        source_kind=cast(CopilotUsageImportKind, str(row["source_kind"])),
        filename=str(row["filename"]),
        content_sha256=str(row["content_sha256"]),
        row_count=int(row["row_count"]),
        inserted_count=int(row["inserted_count"]),
        duplicate_count=int(row["duplicate_count"]),
        first_usage_date=cast(date, row["first_usage_date"]),
        last_usage_date=cast(date, row["last_usage_date"]),
        created_at=cast(datetime, row["created_at"]),
    )


def _cost_center_request(row: Mapping[str, Any]) -> CopilotCostCenterRequest:
    return CopilotCostCenterRequest.model_validate(
        {field: row.get(field) for field in CopilotCostCenterRequest.model_fields}
    )
