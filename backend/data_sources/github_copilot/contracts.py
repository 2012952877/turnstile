from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator

from ...domain.models import StrictModel


class CopilotConnectionSummary(StrictModel):
    id: UUID
    organization: str
    display_name: str
    credential_configured: bool
    credential_hint: str | None = None
    write_enabled: bool
    is_default: bool


class CopilotStatus(StrictModel):
    configured: bool
    oauth_configured: bool = False
    viewer_role: Literal["owner", "member"]
    viewer_github_login: str | None
    connections: list[CopilotConnectionSummary]


class CopilotOAuthConfigWrite(StrictModel):
    client_id: str = Field(min_length=1, max_length=255)
    client_secret: SecretStr | None = Field(default=None, min_length=1)
    callback_url: str | None = Field(default=None, max_length=1000)

    @field_validator("client_id", "callback_url")
    @classmethod
    def strip_oauth_value(cls, value: str | None) -> str | None:
        return value.strip() if value else None


class CopilotOAuthConfigSummary(StrictModel):
    configured: bool
    client_id: str | None = None
    client_secret_hint: str | None = None
    callback_url: str | None = None
    effective_callback_url: str


class CopilotConnectionWrite(StrictModel):
    organization: str = Field(
        min_length=1,
        max_length=39,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$",
    )
    token: SecretStr = Field(min_length=8)
    write_enabled: bool = False
    set_default: bool = True

    @field_validator("organization")
    @classmethod
    def normalize_organization(cls, value: str) -> str:
        return value.strip().lower()


class CopilotIdentityWrite(StrictModel):
    github_login: str = Field(
        min_length=1,
        max_length=39,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$",
    )

    @field_validator("github_login")
    @classmethod
    def normalize_login(cls, value: str) -> str:
        return value.strip().lower()


class CopilotIdentityMapping(StrictModel):
    app_user_id: UUID
    email: str
    display_name: str | None
    github_login: str | None


class CopilotUsageTotals(StrictModel):
    seats: int = Field(default=0, ge=0)
    active_users: int = Field(default=0, ge=0)
    interactions: int = Field(default=0, ge=0)
    generations: int = Field(default=0, ge=0)
    acceptances: int = Field(default=0, ge=0)
    acceptance_rate: float = Field(default=0, ge=0, le=100)
    loc_added: int = Field(default=0, ge=0)
    loc_deleted: int = Field(default=0, ge=0)
    ai_credits_used: float = Field(default=0, ge=0)
    gross_amount: float = Field(default=0, ge=0)
    net_amount: float = Field(default=0, ge=0)
    cli_prompt_tokens: int = Field(default=0, ge=0)
    cli_output_tokens: int = Field(default=0, ge=0)


class CopilotDailyUsage(StrictModel):
    day: date
    active_users: int = Field(default=0, ge=0)
    weekly_active_users: int = Field(default=0, ge=0)
    monthly_active_users: int = Field(default=0, ge=0)
    agent_users: int = Field(default=0, ge=0)
    chat_users: int = Field(default=0, ge=0)
    interactions: int = Field(default=0, ge=0)
    generations: int = Field(default=0, ge=0)
    acceptances: int = Field(default=0, ge=0)
    loc_added: int = Field(default=0, ge=0)
    loc_deleted: int = Field(default=0, ge=0)
    ai_credits_used: float = Field(default=0, ge=0)


class CopilotBreakdownItem(StrictModel):
    key: str
    label: str
    interactions: int = Field(default=0, ge=0)
    generations: int = Field(default=0, ge=0)
    acceptances: int = Field(default=0, ge=0)
    loc_added: int = Field(default=0, ge=0)
    loc_deleted: int = Field(default=0, ge=0)


class CopilotAiCreditItem(StrictModel):
    model: str
    gross_quantity: float = Field(default=0, ge=0)
    discount_quantity: float = Field(default=0, ge=0)
    net_quantity: float = Field(default=0, ge=0)
    net_cost: float = Field(default=0, ge=0)


class CopilotMemberUsage(StrictModel):
    login: str
    avatar_url: str | None = None
    plan_type: str
    seat_created_at: datetime | None = None
    last_activity_at: datetime | None = None
    last_activity_editor: str | None = None
    pending_cancellation_date: date | None = None
    assigning_team: str | None = None
    adoption_phase: str | None = None
    budget_amount: float | None = Field(default=None, ge=0)
    budget_consumed: float | None = Field(default=None, ge=0)
    budget_remaining: float | None = None
    totals: CopilotUsageTotals


class CopilotSubscription(StrictModel):
    plan_type: str
    seat_management_setting: str | None = None
    seat_breakdown: dict[str, int]
    estimated_monthly_seat_cost: float = Field(default=0, ge=0)
    price_per_seat: float = Field(default=0, ge=0)


class CopilotDashboard(StrictModel):
    organization: str
    report_start_day: date | None
    report_end_day: date | None
    generated_at: datetime
    viewer_github_login: str | None
    can_view_members: bool
    subscription: CopilotSubscription
    totals: CopilotUsageTotals
    daily: list[CopilotDailyUsage]
    models: list[CopilotBreakdownItem]
    features: list[CopilotBreakdownItem]
    languages: list[CopilotBreakdownItem]
    ides: list[CopilotBreakdownItem]
    ai_credit_breakdown: list[CopilotAiCreditItem] = Field(default_factory=list)
    members: list[CopilotMemberUsage]
    warnings: list[str] = Field(default_factory=list)


class CopilotGovernanceMember(StrictModel):
    login: str
    avatar_url: str | None = None
    has_seat: bool
    last_activity_at: datetime | None = None
    last_activity_editor: str | None = None
    plan_type: str | None = None


class CopilotTeamSummary(StrictModel):
    slug: str
    name: str
    description: str | None = None
    organization_selection_type: str | None = None
    organizations: list[str] = Field(default_factory=list)
    members: list[CopilotGovernanceMember] = Field(default_factory=list)
    member_count: int = Field(ge=0)
    seat_count: int = Field(ge=0)


class CopilotCostCenterResource(StrictModel):
    type: str
    name: str


class CopilotCostCenterSummary(StrictModel):
    id: str
    name: str
    state: Literal["active", "archived"]
    resources: list[CopilotCostCenterResource] = Field(default_factory=list)
    members: list[CopilotGovernanceMember] = Field(default_factory=list)
    member_count: int = Field(ge=0)


class CopilotBudgetSummary(StrictModel):
    id: str
    budget_type: str
    scope: str
    entity_name: str
    product_skus: list[str] = Field(default_factory=list)
    amount: float = Field(ge=0)
    consumed_amount: float | None = Field(default=None, ge=0)
    remaining_amount: float | None = None
    usage_percent: float | None = Field(default=None, ge=0)
    prevent_further_usage: bool
    will_alert: bool
    alert_recipients: list[str] = Field(default_factory=list)


class CopilotGovernance(StrictModel):
    organization: str
    generated_at: datetime
    write_enabled: bool
    seats: list[CopilotGovernanceMember] = Field(default_factory=list)
    teams: list[CopilotTeamSummary] = Field(default_factory=list)
    cost_centers: list[CopilotCostCenterSummary] = Field(default_factory=list)
    unassigned_seats: list[CopilotGovernanceMember] = Field(default_factory=list)
    budgets: list[CopilotBudgetSummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


CopilotUsageImportKind = Literal["ai_usage", "usage_report"]


class CopilotUsageImportSummary(StrictModel):
    upload_id: UUID
    source_kind: CopilotUsageImportKind
    filename: str
    content_sha256: str
    row_count: int = Field(ge=1)
    inserted_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    first_usage_date: date
    last_usage_date: date
    created_at: datetime


class CopilotImportedUsageDaily(StrictModel):
    day: date
    quantity: float = Field(ge=0)
    gross_amount: float = Field(ge=0)
    net_amount: float = Field(ge=0)
    active_users: int = Field(ge=0)


class CopilotImportedUsageBreakdown(StrictModel):
    key: str
    quantity: float = Field(ge=0)
    gross_amount: float = Field(ge=0)
    net_amount: float = Field(ge=0)
    user_count: int = Field(ge=0)


class CopilotImportedUsageUser(StrictModel):
    login: str
    organization: str
    cost_center_name: str | None = None
    quantity: float = Field(ge=0)
    gross_amount: float = Field(ge=0)
    net_amount: float = Field(ge=0)
    active_days: int = Field(ge=0)
    monthly_quota: float | None = Field(default=None, ge=0)
    usage_percent: float | None = Field(default=None, ge=0)


class CopilotImportedUsage(StrictModel):
    source_kind: CopilotUsageImportKind
    has_data: bool
    first_usage_date: date | None = None
    last_usage_date: date | None = None
    total_quantity: float = Field(default=0, ge=0)
    total_gross_amount: float = Field(default=0, ge=0)
    total_net_amount: float = Field(default=0, ge=0)
    unique_users: int = Field(default=0, ge=0)
    unique_organizations: int = Field(default=0, ge=0)
    daily: list[CopilotImportedUsageDaily] = Field(default_factory=list)
    primary_breakdown: list[CopilotImportedUsageBreakdown] = Field(default_factory=list)
    product_breakdown: list[CopilotImportedUsageBreakdown] = Field(default_factory=list)
    organization_breakdown: list[CopilotImportedUsageBreakdown] = Field(
        default_factory=list
    )
    cost_center_breakdown: list[CopilotImportedUsageBreakdown] = Field(
        default_factory=list
    )
    users: list[CopilotImportedUsageUser] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    cost_centers: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    skus: list[str] = Field(default_factory=list)


class CopilotCostCenterOption(StrictModel):
    id: str
    name: str
    state: Literal["active", "archived"]


class CopilotCostCenterRequestCreate(StrictModel):
    organization: str = Field(min_length=1, max_length=39)
    cost_center_id: str = Field(min_length=1, max_length=255)
    reason: str = Field(default="", max_length=1000)

    @field_validator("organization")
    @classmethod
    def normalize_cost_center_organization(cls, value: str) -> str:
        return value.strip().lower()


class CopilotCostCenterRequestReview(StrictModel):
    decision: Literal["approve", "reject"]
    comment: str = Field(default="", max_length=1000)
    apply_to_github: bool = False


class CopilotCostCenterRequest(StrictModel):
    id: UUID
    organization: str
    app_user_id: UUID
    user_email: str
    user_display_name: str | None
    github_login: str
    cost_center_id: str
    cost_center_name: str
    reason: str
    status: BudgetRequestStatus
    github_sync_status: Literal["not_requested", "skipped", "updated", "failed"]
    github_sync_error: str | None
    reviewed_by: str | None
    review_comment: str | None
    created_at: datetime
    updated_at: datetime
    reviewed_at: datetime | None


class CopilotCostCenterRequestList(StrictModel):
    items: list[CopilotCostCenterRequest]
    can_review: bool
    pending_count: int = Field(ge=0)
    approved_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)


BudgetRequestStatus = Literal["pending", "approved", "rejected"]
BudgetSyncStatus = Literal["not_requested", "skipped", "created", "updated", "failed"]


class CopilotBudgetRequestCreate(StrictModel):
    organization: str = Field(min_length=1, max_length=39)
    amount_usd: int = Field(ge=1, le=1_000_000)
    reason: str = Field(default="", max_length=1000)

    @field_validator("organization")
    @classmethod
    def normalize_organization(cls, value: str) -> str:
        return value.strip().lower()


class CopilotBudgetRequestReview(StrictModel):
    decision: Literal["approve", "reject"]
    approved_amount_usd: int | None = Field(default=None, ge=1, le=1_000_000)
    comment: str = Field(default="", max_length=1000)
    apply_to_github: bool = False


class CopilotBudgetRequest(StrictModel):
    id: UUID
    organization: str
    app_user_id: UUID
    user_email: str
    user_display_name: str | None
    github_login: str
    requested_amount_usd: int
    approved_amount_usd: int | None
    reason: str
    status: BudgetRequestStatus
    github_sync_status: BudgetSyncStatus
    github_sync_error: str | None
    reviewed_by: str | None
    review_comment: str | None
    created_at: datetime
    updated_at: datetime
    reviewed_at: datetime | None


class CopilotBudgetRequestList(StrictModel):
    items: list[CopilotBudgetRequest]
    can_review: bool
    pending_count: int = Field(ge=0)
    approved_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
