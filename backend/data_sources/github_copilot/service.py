from __future__ import annotations

import hashlib
import secrets
from asyncio import Semaphore, gather
from collections import defaultdict
from collections.abc import Awaitable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, cast
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import httpx

from ...security import CredentialCipher, credential_hint
from .client import (
    COPILOT_REPORT_WINDOW_DAYS,
    GITHUB_API_BASE_URL,
    GITHUB_API_VERSION,
    GitHubCopilotApiError,
    GitHubCopilotClient,
    total,
)
from .contracts import (
    CopilotBudgetRequest,
    CopilotBudgetRequestCreate,
    CopilotBudgetRequestList,
    CopilotBudgetRequestReview,
    CopilotConnectionSummary,
    CopilotConnectionWrite,
    CopilotCostCenterOption,
    CopilotCostCenterRequest,
    CopilotCostCenterRequestCreate,
    CopilotCostCenterRequestList,
    CopilotCostCenterRequestReview,
    CopilotCostCenterResource,
    CopilotCostCenterSummary,
    CopilotDashboard,
    CopilotGovernance,
    CopilotIdentityMapping,
    CopilotImportedUsage,
    CopilotImportedUsageBreakdown,
    CopilotImportedUsageDaily,
    CopilotImportedUsageUser,
    CopilotOAuthConfigSummary,
    CopilotOAuthConfigWrite,
    CopilotStatus,
    CopilotSubscription,
    CopilotTeamSummary,
    CopilotUsageImportKind,
    CopilotUsageImportSummary,
)
from .csv_import import ParsedCopilotCsv
from .service_support import (
    PLAN_PRICES_USD,
    CopilotApiFactory,
    CopilotApiProtocol,
    CopilotIdentityRequiredError,
    CopilotNotConfiguredError,
    CopilotNotFoundError,
    CopilotPermissionError,
    CopilotStoreProtocol,
    OAuthClientFactory,
    _ai_credit_breakdown,
    _breakdown,
    _budget_for_login,
    _budget_request,
    _budget_summary,
    _cost_center_request,
    _daily_rows,
    _daily_usage,
    _date,
    _datetime,
    _governance_member,
    _integer,
    _member,
    _number,
    _usage_import,
    _usage_totals,
)
from .store import CopilotStoreConflictError

GITHUB_OAUTH_BASE_URL = "https://github.com/login/oauth"
GITHUB_OAUTH_TIMEOUT_SECONDS = 20

__all__ = (
    "CopilotApiFactory",
    "CopilotApiProtocol",
    "CopilotIdentityRequiredError",
    "CopilotNotConfiguredError",
    "CopilotNotFoundError",
    "CopilotPermissionError",
    "CopilotService",
    "CopilotStoreProtocol",
    "OAuthClientFactory",
)


class CopilotService:
    def __init__(
        self,
        store: CopilotStoreProtocol,
        cipher: CredentialCipher,
        client_factory: CopilotApiFactory | None = None,
        oauth_client_factory: OAuthClientFactory | None = None,
    ) -> None:
        self._store = store
        self._cipher = cipher
        self._client_factory = client_factory or (lambda token: GitHubCopilotClient(token))
        self._oauth_client_factory = oauth_client_factory or (
            lambda: httpx.AsyncClient(
                timeout=GITHUB_OAUTH_TIMEOUT_SECONDS,
                trust_env=False,
                headers={"User-Agent": "Turnstile-FinOps"},
            )
        )

    @staticmethod
    def _connection_summary(
        row: Mapping[str, Any], *, disclose_admin: bool = True
    ) -> CopilotConnectionSummary:
        return CopilotConnectionSummary(
            id=row["id"],
            organization=str(row["organization"]),
            display_name=str(row["display_name"]),
            credential_configured=bool(row.get("credential_hint")),
            credential_hint=(
                str(row["credential_hint"])
                if disclose_admin and row.get("credential_hint")
                else None
            ),
            write_enabled=bool(row.get("write_enabled")) if disclose_admin else False,
            is_default=bool(row.get("is_default")),
        )

    def status(self, user_id: UUID, role: str, origin: str = "") -> CopilotStatus:
        identity = self._store.identity(user_id)
        rows = list(self._store.list_connections())
        if role != "owner":
            default = next((row for row in rows if row.get("is_default")), None)
            rows = [default] if default is not None else []
        return CopilotStatus(
            configured=bool(rows),
            oauth_configured=bool(origin and self._store.oauth_setting(origin)),
            viewer_role="owner" if role == "owner" else "member",
            viewer_github_login=(str(identity["github_login"]) if identity else None),
            connections=[
                self._connection_summary(row, disclose_admin=role == "owner")
                for row in rows
            ],
        )

    @staticmethod
    def _callback_url(origin: str, configured: str | None) -> str:
        return configured or f"{origin}/api/v1/copilot/oauth/callback"

    def oauth_config(self, origin: str) -> CopilotOAuthConfigSummary:
        row = self._store.oauth_setting(origin)
        return CopilotOAuthConfigSummary(
            configured=row is not None,
            client_id=str(row["client_id"]) if row else None,
            client_secret_hint=str(row["client_secret_hint"]) if row else None,
            callback_url=str(row["callback_url"]) if row and row.get("callback_url") else None,
            effective_callback_url=self._callback_url(
                origin, str(row["callback_url"]) if row and row.get("callback_url") else None
            ),
        )

    def save_oauth_config(
        self, write: CopilotOAuthConfigWrite, origin: str, actor: str
    ) -> CopilotOAuthConfigSummary:
        existing = self._store.oauth_setting(origin)
        secret = write.client_secret.get_secret_value() if write.client_secret else None
        if secret is None and existing is None:
            raise CopilotNotConfiguredError("GitHub OAuth client secret is required")
        callback = write.callback_url
        if callback:
            parsed = urlsplit(callback)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("GitHub OAuth callback URL is invalid")
            if f"{parsed.scheme}://{parsed.netloc}" != origin:
                raise ValueError("GitHub OAuth callback must use this site's origin")
        if secret is not None:
            ciphertext = self._cipher.encrypt(secret)
            hint = credential_hint(secret)
        else:
            assert existing is not None
            ciphertext = bytes(existing["client_secret_ciphertext"])
            hint = str(existing["client_secret_hint"])
        self._store.save_oauth_setting(
            origin=origin,
            client_id=write.client_id,
            client_secret_ciphertext=ciphertext,
            client_secret_hint=hint,
            callback_url=callback,
            updated_by=actor,
        )
        return self.oauth_config(origin)

    def oauth_authorize_url(
        self,
        user_id: UUID,
        origin: str,
        return_origin: str,
        return_path: str,
        purpose: Literal["identity", "organization"] = "identity",
        organization: str | None = None,
    ) -> str:
        row = self._store.oauth_setting(origin)
        if row is None:
            raise CopilotNotConfiguredError("GitHub OAuth is not configured")
        safe_return = (
            return_path
            if return_path.startswith("/") and not return_path.startswith("//")
            else "/?page=finops-overview&source=github-copilot"
        )
        state = secrets.token_urlsafe(32)
        self._store.create_oauth_state(
            hashlib.sha256(state.encode()).hexdigest(),
            user_id,
            origin,
            return_origin,
            safe_return,
            purpose,
            organization,
            datetime.now(UTC) + timedelta(minutes=10),
        )
        callback = self._callback_url(origin, row.get("callback_url"))
        scopes = ["read:user"]
        if purpose == "organization":
            scopes.append("manage_billing:copilot")
        else:
            scopes.append("read:org")
        return f"{GITHUB_OAUTH_BASE_URL}/authorize?" + urlencode(
            {
                "client_id": row["client_id"],
                "redirect_uri": callback,
                "scope": " ".join(scopes),
                "state": state,
                "allow_signup": "false",
            }
        )

    async def complete_oauth_link(
        self,
        *,
        origin: str,
        state: str,
        code: str,
    ) -> str:
        oauth_state = self._store.consume_oauth_state(
            hashlib.sha256(state.encode()).hexdigest(), origin
        )
        if oauth_state is None:
            raise CopilotPermissionError("GitHub OAuth state is invalid or expired")
        user_id = UUID(str(oauth_state["app_user_id"]))
        user_email = str(oauth_state["email"])
        user_role = str(oauth_state["role"])
        return_origin = str(oauth_state["return_origin"])
        return_path = str(oauth_state["return_path"])
        purpose = str(oauth_state["purpose"])
        organization = (
            str(oauth_state["organization"]).strip().lower()
            if oauth_state.get("organization")
            else None
        )
        row = self._store.oauth_setting(origin)
        if row is None:
            raise CopilotNotConfiguredError("GitHub OAuth is not configured")
        secret = self._cipher.decrypt(row.get("client_secret_ciphertext"))
        if not secret:
            raise CopilotNotConfiguredError("GitHub OAuth client secret is unavailable")
        callback = self._callback_url(origin, row.get("callback_url"))
        try:
            async with self._oauth_client_factory() as client:
                token_response = await client.post(
                    f"{GITHUB_OAUTH_BASE_URL}/access_token",
                    headers={"Accept": "application/json"},
                    data={
                        "client_id": row["client_id"],
                        "client_secret": secret,
                        "code": code,
                        "redirect_uri": callback,
                    },
                )
                token_response.raise_for_status()
                access_token = token_response.json().get("access_token")
                if not access_token:
                    raise CopilotPermissionError("GitHub OAuth token exchange failed")
                headers = {
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {access_token}",
                    "X-GitHub-Api-Version": GITHUB_API_VERSION,
                }
                profile_response = await client.get(
                    f"{GITHUB_API_BASE_URL}/user", headers=headers
                )
                profile_response.raise_for_status()
                login = str(profile_response.json().get("login") or "").strip().lower()
                if not login:
                    raise CopilotPermissionError("GitHub did not return a login")
                organization_profiles: list[dict[str, Any]] = []
                if purpose == "organization":
                    if user_role != "owner":
                        raise CopilotPermissionError(
                            "Owner role is required to connect organization data"
                        )
                    candidates = [organization] if organization else []
                    if organization is None:
                        organizations_response = await client.get(
                            f"{GITHUB_API_BASE_URL}/user/orgs",
                            headers=headers,
                            params={"per_page": 100},
                        )
                        organizations_response.raise_for_status()
                        discovered = organizations_response.json()
                        if isinstance(discovered, list):
                            candidates.extend(
                                str(item.get("login") or "").strip().lower()
                                for item in discovered
                                if isinstance(item, Mapping)
                            )
                    seen: set[str] = set()
                    for candidate in candidates:
                        if not candidate or candidate in seen:
                            continue
                        seen.add(candidate)
                        organization_response = await client.get(
                            f"{GITHUB_API_BASE_URL}/orgs/{candidate}",
                            headers=headers,
                        )
                        if organization_response.status_code == 404:
                            enterprise_seats_response = await client.get(
                                f"{GITHUB_API_BASE_URL}/enterprises/"
                                f"{candidate}/copilot/billing/seats",
                                headers=headers,
                                params={"per_page": 1},
                            )
                            if enterprise_seats_response.status_code == 200:
                                organization_profiles.append(
                                    {"login": candidate, "name": candidate}
                                )
                            elif enterprise_seats_response.status_code not in {403, 404}:
                                enterprise_seats_response.raise_for_status()
                            continue
                        if organization_response.status_code != 200:
                            organization_response.raise_for_status()
                            continue
                        membership_response = await client.get(
                            f"{GITHUB_API_BASE_URL}/user/memberships/orgs/"
                            f"{candidate}",
                            headers=headers,
                        )
                        if (
                            membership_response.status_code != 200
                            or membership_response.json().get("state") != "active"
                        ):
                            continue
                        billing_response = await client.get(
                            f"{GITHUB_API_BASE_URL}/orgs/"
                            f"{candidate}/copilot/billing",
                            headers=headers,
                        )
                        metrics_response = await client.get(
                            f"{GITHUB_API_BASE_URL}/orgs/"
                            f"{candidate}/copilot/metrics/reports/"
                            f"organization-{COPILOT_REPORT_WINDOW_DAYS}-day/latest",
                            headers=headers,
                        )
                        if billing_response.status_code not in {200, 403, 404}:
                            billing_response.raise_for_status()
                        if metrics_response.status_code not in {200, 204, 403, 404}:
                            metrics_response.raise_for_status()
                        if 200 not in {
                            billing_response.status_code,
                            metrics_response.status_code,
                        }:
                            continue
                        profile = organization_response.json()
                        if isinstance(profile, dict):
                            organization_profiles.append(profile)
                    if not organization_profiles:
                        raise CopilotPermissionError(
                            "No OAuth-visible organization exposes Copilot data"
                        )
                default = next(
                    (
                        item
                        for item in self._store.list_connections()
                        if item.get("is_default")
                    ),
                    None,
                )
                if default is not None and purpose == "identity":
                    membership = await client.get(
                        f"{GITHUB_API_BASE_URL}/user/memberships/orgs/"
                        f"{default['organization']}",
                        headers=headers,
                    )
                    active_organization_member = (
                        membership.status_code == 200
                        and membership.json().get("state") == "active"
                    )
                    active_enterprise_seat = False
                    if membership.status_code == 404:
                        owner_token = self._cipher.decrypt(
                            default.get("credential_ciphertext")
                        )
                        if owner_token:
                            owner_client = self._client_factory(owner_token)
                            try:
                                await owner_client.member_seat(
                                    str(default["organization"]), login
                                )
                                active_enterprise_seat = True
                            except GitHubCopilotApiError as error:
                                if error.status_code not in {403, 404}:
                                    raise
                            finally:
                                await owner_client.aclose()
                    if not active_organization_member and not active_enterprise_seat:
                        raise CopilotPermissionError(
                            "The GitHub account is not an active member or Copilot "
                            "seat holder of the configured account"
                        )
        except httpx.HTTPError as error:
            raise GitHubCopilotApiError(502, "GitHub OAuth request failed") from error
        self._store.save_identity(user_id, login, user_email)
        if purpose == "organization":
            for index, organization_profile in enumerate(organization_profiles):
                canonical_organization = str(
                    organization_profile.get("login") or ""
                ).strip().lower()
                if not canonical_organization:
                    continue
                self._store.save_connection(
                    organization=canonical_organization,
                    display_name=str(
                        organization_profile.get("name") or canonical_organization
                    ).strip(),
                    credential_ciphertext=self._cipher.encrypt(str(access_token)),
                    credential_hint="GitHub OAuth",
                    write_enabled=False,
                    set_default=index == 0,
                    updated_by=user_email,
                )
        separator = "&" if "?" in return_path else "?"
        return f"{return_origin}{return_path}{separator}github_linked=1"

    async def save_connection(
        self, write: CopilotConnectionWrite, actor: str
    ) -> CopilotConnectionSummary:
        token = write.token.get_secret_value()
        client = self._client_factory(token)
        enterprise = False
        try:
            await client.profile()
            try:
                account = await client.organization(write.organization)
                await client.billing(write.organization)
            except GitHubCopilotApiError as error:
                if error.status_code != 404:
                    raise
                enterprise = True
                now = datetime.now(UTC)
                await client.seats(write.organization)
                await client.ai_credit_usage(
                    write.organization, now.year, now.month
                )
                await client.billing_usage(
                    write.organization, now.year, now.month
                )
                await client.budgets(write.organization)
                account = {
                    "login": write.organization,
                    "name": write.organization,
                }
        finally:
            await client.aclose()
        login = str(account.get("login") or "").strip().lower()
        if login != write.organization:
            raise GitHubCopilotApiError(502, "GitHub returned a different organization")
        existing = self._store.connection(login)
        display_name = str(
            (existing or {}).get("display_name") or account.get("name") or login
        ).strip()
        row = self._store.save_connection(
            organization=login,
            display_name=display_name,
            credential_ciphertext=self._cipher.encrypt(token),
            credential_hint=(
                f"Billing PAT {credential_hint(token)}"
                if enterprise
                else credential_hint(token)
            ),
            write_enabled=write.write_enabled,
            set_default=write.set_default,
            updated_by=actor,
        )
        return self._connection_summary(row)

    def identities(self) -> list[CopilotIdentityMapping]:
        return [CopilotIdentityMapping.model_validate(row) for row in self._store.list_identities()]

    def save_identity(
        self, app_user_id: UUID, github_login: str, actor: str
    ) -> CopilotIdentityMapping:
        return CopilotIdentityMapping.model_validate(
            self._store.save_identity(app_user_id, github_login, actor)
        )

    def _connection(self, organization: str | None) -> dict[str, Any]:
        row = self._store.connection(organization)
        if row is None:
            raise CopilotNotConfiguredError("GitHub Copilot organization is not configured")
        return row

    def _connection_for_viewer(
        self, organization: str | None, role: str
    ) -> dict[str, Any]:
        if role == "owner":
            return self._connection(organization)
        default = next(
            (
                row
                for row in self._store.list_connections()
                if row.get("is_default")
            ),
            None,
        )
        if default is None:
            raise CopilotNotConfiguredError(
                "A default GitHub Copilot organization is not configured"
            )
        if organization and organization.strip().lower() != str(default["organization"]):
            raise CopilotPermissionError(
                "Members can use only the default GitHub Copilot organization"
            )
        return default

    def _login(self, user_id: UUID, role: str) -> str | None:
        if role == "owner":
            return None
        identity = self._store.identity(user_id)
        if identity is None:
            raise CopilotIdentityRequiredError(
                "Your application account is not linked to a GitHub login"
            )
        return str(identity["github_login"])

    async def dashboard(
        self, *, user_id: UUID, role: str, organization: str | None = None
    ) -> CopilotDashboard:
        connection = self._connection_for_viewer(organization, role)
        org = str(connection["organization"])
        login = self._login(user_id, role)
        token = self._cipher.decrypt(connection.get("credential_ciphertext"))
        if not token:
            raise CopilotNotConfiguredError("GitHub Copilot credential is unavailable")
        client = self._client_factory(token)
        warnings: list[str] = []
        now = datetime.now(UTC)
        try:
            try:
                billing = await client.billing(org)
            except GitHubCopilotApiError as error:
                if error.status_code not in {403, 404}:
                    raise
                billing = {}
                warnings.append(
                    "Copilot subscription and seat billing are unavailable"
                )
            if login is None:
                try:
                    seats = await client.seats(org)
                except GitHubCopilotApiError as error:
                    if error.status_code not in {403, 404}:
                        raise
                    seats = []
                    warnings.append("Copilot seat assignments are unavailable")
            else:
                try:
                    seats = [await client.member_seat(org, login)]
                except GitHubCopilotApiError as error:
                    if error.status_code in {403, 404}:
                        seats = []
                        warnings.append("Your Copilot seat details are unavailable")
                    else:
                        raise

            organization_meta: dict[str, Any] = {}
            organization_records: list[dict[str, Any]] = []
            if login is None:
                try:
                    organization_meta, raw_organization_records = await client.usage_report(
                        org, "organization"
                    )
                    organization_records = raw_organization_records
                    if organization_meta.get("legacy_metrics"):
                        warnings.append(
                            "Copilot usage uses the legacy daily metrics API because "
                            "enterprise usage reports are disabled"
                        )
                except GitHubCopilotApiError as error:
                    if error.status_code not in {403, 404}:
                        raise
                    warnings.append(
                        "Copilot usage metrics are unavailable for this organization"
                    )

            user_meta: dict[str, Any] = {}
            user_records: list[dict[str, Any]] = []
            try:
                user_meta, raw_user_records = await client.usage_report(org, "users")
                user_records = raw_user_records
            except GitHubCopilotApiError as error:
                if error.status_code not in {403, 404}:
                    raise
                warnings.append("Member-level Copilot usage metrics are unavailable")

            if login is not None:
                user_records = [
                    record
                    for record in user_records
                    if str(record.get("user_login", "")).lower() == login.lower()
                ]
                activity_records: Sequence[Mapping[str, Any]] = user_records
            else:
                activity_records = _daily_rows(organization_records) or user_records

            try:
                ai_credit = await client.ai_credit_usage(
                    org, now.year, now.month, user=login
                )
            except GitHubCopilotApiError as error:
                if error.status_code not in {403, 404}:
                    raise
                ai_credit = {"usageItems": []}
                warnings.append("Current-month AI credit billing is unavailable")
            usage_items = ai_credit.get("usageItems")
            billing_items = (
                [item for item in usage_items if isinstance(item, Mapping)]
                if isinstance(usage_items, list)
                else []
            )

            cost_items = billing_items
            if login is None:
                try:
                    billing_usage = await client.billing_usage(
                        org, now.year, now.month
                    )
                    raw_cost_items = billing_usage.get("usageItems")
                    if isinstance(raw_cost_items, list):
                        normalized_cost_items = [
                            item for item in raw_cost_items if isinstance(item, Mapping)
                        ]
                        if normalized_cost_items:
                            cost_items = normalized_cost_items
                except GitHubCopilotApiError as error:
                    if error.status_code not in {403, 404}:
                        raise
                    warnings.append("Current-month Copilot billing is unavailable")

            try:
                budgets = await client.budgets(org, user=login)
            except GitHubCopilotApiError as error:
                if error.status_code not in {403, 404}:
                    raise
                budgets = []
                warnings.append("GitHub user budgets are unavailable")
        finally:
            await client.aclose()

        plan_type = str(billing.get("plan_type") or "unknown").lower()
        seat_price = PLAN_PRICES_USD.get(plan_type, 0.0)
        seat_breakdown = billing.get("seat_breakdown")
        normalized_seat_breakdown = {
            str(key): _integer(value)
            for key, value in seat_breakdown.items()
            if isinstance(key, str)
        } if isinstance(seat_breakdown, Mapping) else {"total": len(seats)}
        if login is not None:
            normalized_seat_breakdown = {"total": len(seats)}

        records_by_login: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for record in user_records:
            record_login = str(record.get("user_login", "")).lower()
            if record_login:
                records_by_login[record_login].append(record)
        members = [
            member
            for seat in seats
            if (member := _member(
                seat,
                records_by_login.get(
                    str(
                        cast(Mapping[str, Any], seat.get("assignee") or {}).get(
                            "login", ""
                        )
                    ).lower(),
                    [],
                ),
                budgets,
            )) is not None
        ]
        gross_amount = total(cost_items, "grossAmount")
        net_amount = total(cost_items, "netAmount")
        measured_credits = sum(
            _number(record.get("netQuantity") or record.get("grossQuantity"))
            for record in billing_items
        )
        active_seat_count = sum(
            1
            for seat in seats
            if (
                (last_activity := _datetime(seat.get("last_activity_at"))) is not None
                and last_activity >= now - timedelta(days=COPILOT_REPORT_WINDOW_DAYS)
            )
        )
        totals = _usage_totals(
            activity_records,
            seats=len(seats),
            gross_amount=gross_amount,
            net_amount=net_amount,
            ai_credits_used=(
                sum(_number(record.get("ai_credits_used")) for record in user_records)
                or measured_credits
            ),
        )
        if not activity_records and active_seat_count:
            totals = totals.model_copy(update={"active_users": active_seat_count})

        report_start = _date(
            organization_meta.get("report_start_day")
            or user_meta.get("report_start_day")
        )
        report_end = _date(
            organization_meta.get("report_end_day") or user_meta.get("report_end_day")
        )
        model_breakdown = _breakdown(activity_records, "totals_by_model_feature", "model")
        if not model_breakdown:
            model_breakdown = _breakdown(activity_records, "totals_by_language_model", "model")
        return CopilotDashboard(
            organization=org,
            report_start_day=report_start,
            report_end_day=report_end,
            generated_at=datetime.now(UTC),
            viewer_github_login=login,
            can_view_members=role == "owner",
            subscription=CopilotSubscription(
                plan_type=plan_type,
                seat_management_setting=(
                    str(billing["seat_management_setting"])
                    if billing.get("seat_management_setting")
                    else None
                ),
                seat_breakdown=normalized_seat_breakdown,
                estimated_monthly_seat_cost=round(len(seats) * seat_price, 2),
                price_per_seat=seat_price,
            ),
            totals=totals,
            daily=_daily_usage(activity_records, user_records),
            models=model_breakdown,
            features=_breakdown(activity_records, "totals_by_feature", "feature"),
            languages=_breakdown(
                activity_records, "totals_by_language_feature", "language"
            ),
            ides=_breakdown(activity_records, "totals_by_ide", "ide"),
            ai_credit_breakdown=_ai_credit_breakdown(billing_items),
            members=members,
            warnings=warnings,
        )

    async def governance(
        self, *, role: str, organization: str | None = None
    ) -> CopilotGovernance:
        if role != "owner":
            raise CopilotPermissionError(
                "Owner role is required to read GitHub enterprise governance"
            )
        connection = self._connection(organization)
        enterprise = str(connection["organization"])
        token = self._cipher.decrypt(connection.get("credential_ciphertext"))
        if not token:
            raise CopilotNotConfiguredError("GitHub Copilot credential is unavailable")
        client = self._client_factory(token)
        warnings: list[str] = []
        seats_raw: list[dict[str, Any]] = []
        teams_raw: list[dict[str, Any]] = []
        cost_centers_raw: list[dict[str, Any]] = []
        budgets_raw: list[dict[str, Any]] = []

        async def read_domain(
            label: str, call: Awaitable[list[dict[str, Any]]]
        ) -> list[dict[str, Any]]:
            try:
                return await call
            except GitHubCopilotApiError as error:
                if error.status_code not in {403, 404}:
                    raise
                warnings.append(f"GitHub {label} are unavailable")
                return []

        try:
            seats_raw, teams_raw, cost_centers_raw, budgets_raw = await gather(
                read_domain("Copilot seats", client.seats(enterprise)),
                read_domain("enterprise teams", client.enterprise_teams(enterprise)),
                read_domain(
                    "cost centers", client.enterprise_cost_centers(enterprise)
                ),
                read_domain("budgets", client.all_budgets(enterprise)),
            )
            seats_by_login: dict[str, Mapping[str, Any]] = {}
            for seat in seats_raw:
                assignee = seat.get("assignee")
                login = (
                    str(assignee.get("login") or "").strip()
                    if isinstance(assignee, Mapping)
                    else ""
                )
                if login:
                    seats_by_login[login.lower()] = seat

            semaphore = Semaphore(6)

            async def bounded(call: Awaitable[list[dict[str, Any]]]) -> list[dict[str, Any]]:
                async with semaphore:
                    return await call

            async def normalize_team(raw: Mapping[str, Any]) -> CopilotTeamSummary:
                slug = str(raw.get("slug") or "").strip()
                members_raw: list[dict[str, Any]] = []
                organizations_raw: list[dict[str, Any]] = []
                if slug:
                    members_raw, organizations_raw = await gather(
                        read_domain(
                            f"memberships for enterprise team {slug}",
                            bounded(
                                client.enterprise_team_memberships(enterprise, slug)
                            ),
                        ),
                        read_domain(
                            f"organizations for enterprise team {slug}",
                            bounded(
                                client.enterprise_team_organizations(enterprise, slug)
                            ),
                        ),
                    )
                members = [
                    _governance_member(login, seats_by_login, member)
                    for member in members_raw
                    if (login := str(member.get("login") or "").strip())
                ]
                members.sort(key=lambda member: member.login.lower())
                organizations = sorted(
                    {
                        str(item.get("login") or item.get("name") or "").strip()
                        for item in organizations_raw
                        if item.get("login") or item.get("name")
                    },
                    key=str.lower,
                )
                return CopilotTeamSummary(
                    slug=slug,
                    name=str(raw.get("name") or slug),
                    description=(
                        str(raw["description"]) if raw.get("description") else None
                    ),
                    organization_selection_type=(
                        str(raw["organization_selection_type"])
                        if raw.get("organization_selection_type")
                        else None
                    ),
                    organizations=organizations,
                    members=members,
                    member_count=len(members),
                    seat_count=sum(member.has_seat for member in members),
                )

            teams = await gather(*(normalize_team(raw) for raw in teams_raw))
            teams = sorted(teams, key=lambda team: (-team.member_count, team.name.lower()))

            member_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}

            async def resource_members(resource: Mapping[str, Any]) -> list[dict[str, Any]]:
                resource_type = str(resource.get("type") or "")
                resource_name = str(resource.get("name") or "").strip()
                if not resource_name:
                    return []
                key = (resource_type, resource_name.lower())
                if key in member_cache:
                    return member_cache[key]
                if resource_type == "User":
                    values = [{"login": resource_name}]
                elif resource_type == "Org":
                    values = await read_domain(
                        f"members for cost center organization {resource_name}",
                        bounded(client.organization_members(resource_name)),
                    )
                elif resource_type == "Team" and "/" in resource_name:
                    org, team_slug = resource_name.split("/", 1)
                    values = await read_domain(
                        f"members for cost center team {resource_name}",
                        bounded(client.organization_team_members(org, team_slug)),
                    )
                else:
                    values = []
                member_cache[key] = values
                return values

            async def normalize_cost_center(
                raw: Mapping[str, Any]
            ) -> CopilotCostCenterSummary:
                raw_resources = raw.get("resources")
                resources = [
                    CopilotCostCenterResource(
                        type=str(resource.get("type") or ""),
                        name=str(resource.get("name") or ""),
                    )
                    for resource in raw_resources
                    if isinstance(resource, Mapping)
                ] if isinstance(raw_resources, list) else []
                expanded = await gather(
                    *(resource_members(resource.model_dump()) for resource in resources)
                )
                profiles: dict[str, Mapping[str, Any]] = {}
                for values in expanded:
                    for profile in values:
                        login = str(profile.get("login") or "").strip()
                        if login:
                            profiles.setdefault(login.lower(), profile)
                members = [
                    _governance_member(
                        str(profile.get("login") or key), seats_by_login, profile
                    )
                    for key, profile in profiles.items()
                ]
                members.sort(key=lambda member: member.login.lower())
                raw_state = str(raw.get("state") or "active").lower()
                state: Literal["active", "archived"] = (
                    "archived" if raw_state in {"archived", "deleted"} else "active"
                )
                return CopilotCostCenterSummary(
                    id=str(raw.get("id") or ""),
                    name=str(raw.get("name") or ""),
                    state=state,
                    resources=resources,
                    members=members,
                    member_count=len(members),
                )

            cost_centers = await gather(
                *(normalize_cost_center(raw) for raw in cost_centers_raw)
            )
            cost_centers = sorted(
                cost_centers,
                key=lambda center: (center.state != "active", center.name.lower()),
            )
            assigned_logins = {
                member.login.lower()
                for center in cost_centers
                if center.state == "active"
                for member in center.members
            }
            seats = [
                _governance_member(
                    str(
                        cast(Mapping[str, Any], seat.get("assignee") or {}).get(
                            "login", key
                        )
                    ),
                    seats_by_login,
                )
                for key, seat in seats_by_login.items()
            ]
            seats.sort(key=lambda member: member.login.lower())
            budgets = sorted(
                (_budget_summary(raw) for raw in budgets_raw),
                key=lambda budget: (budget.scope, budget.entity_name.lower()),
            )
            return CopilotGovernance(
                organization=enterprise,
                generated_at=datetime.now(UTC),
                write_enabled=bool(connection.get("write_enabled")),
                seats=seats,
                teams=teams,
                cost_centers=cost_centers,
                unassigned_seats=[
                    member
                    for member in seats
                    if member.login.lower() not in assigned_logins
                ],
                budgets=budgets,
                warnings=warnings,
            )
        finally:
            await client.aclose()

    def save_usage_import(
        self,
        parsed: ParsedCopilotCsv,
        *,
        filename: str,
        file_size_bytes: int,
        user_id: UUID,
        user_email: str,
        organization: str | None = None,
    ) -> CopilotUsageImportSummary:
        connection = self._connection(organization)
        row = self._store.save_usage_import(
            connection_id=connection["id"],
            source_kind=parsed.source_kind,
            filename=filename.strip(),
            content_sha256=parsed.content_sha256,
            file_size_bytes=file_size_bytes,
            rows=parsed.rows,
            first_usage_date=parsed.first_usage_date,
            last_usage_date=parsed.last_usage_date,
            uploaded_by=user_id,
            uploaded_by_email=user_email,
        )
        return _usage_import(row)

    def usage_imports(
        self, source_kind: CopilotUsageImportKind, organization: str | None = None
    ) -> list[CopilotUsageImportSummary]:
        connection = self._connection(organization)
        return [
            _usage_import(row)
            for row in self._store.usage_imports(connection["id"], source_kind)
        ]

    def imported_usage(
        self, source_kind: CopilotUsageImportKind, organization: str | None = None
    ) -> CopilotImportedUsage:
        connection = self._connection(organization)
        result = self._store.imported_usage(connection["id"], source_kind)
        totals = cast(Mapping[str, Any], result.get("totals") or {})
        users = [
            CopilotImportedUsageUser(
                **row,
                usage_percent=(
                    round(_number(row.get("quantity")) / _number(row.get("monthly_quota")) * 100, 1)
                    if _number(row.get("monthly_quota")) > 0
                    else None
                ),
            )
            for row in cast(Sequence[Mapping[str, Any]], result.get("users") or [])
        ]
        filters = cast(Mapping[str, Any], result.get("filters") or {})
        first_usage_date = totals.get("first_usage_date")
        return CopilotImportedUsage(
            source_kind=source_kind,
            has_data=first_usage_date is not None,
            first_usage_date=cast(date | None, first_usage_date),
            last_usage_date=cast(date | None, totals.get("last_usage_date")),
            total_quantity=_number(totals.get("total_quantity")),
            total_gross_amount=_number(totals.get("total_gross_amount")),
            total_net_amount=_number(totals.get("total_net_amount")),
            unique_users=_integer(totals.get("unique_users")),
            unique_organizations=_integer(totals.get("unique_organizations")),
            daily=[
                CopilotImportedUsageDaily.model_validate(row)
                for row in cast(Sequence[Mapping[str, Any]], result.get("daily") or [])
            ],
            primary_breakdown=[
                CopilotImportedUsageBreakdown.model_validate(row)
                for row in cast(
                    Sequence[Mapping[str, Any]], result.get("primary_breakdown") or []
                )
            ],
            product_breakdown=[
                CopilotImportedUsageBreakdown.model_validate(row)
                for row in cast(
                    Sequence[Mapping[str, Any]], result.get("product_breakdown") or []
                )
            ],
            organization_breakdown=[
                CopilotImportedUsageBreakdown.model_validate(row)
                for row in cast(
                    Sequence[Mapping[str, Any]],
                    result.get("organization_breakdown") or [],
                )
            ],
            cost_center_breakdown=[
                CopilotImportedUsageBreakdown.model_validate(row)
                for row in cast(
                    Sequence[Mapping[str, Any]],
                    result.get("cost_center_breakdown") or [],
                )
            ],
            users=users,
            organizations=[str(value) for value in filters.get("organizations") or []],
            cost_centers=[str(value) for value in filters.get("cost_centers") or []],
            products=[str(value) for value in filters.get("products") or []],
            skus=[str(value) for value in filters.get("skus") or []],
        )

    async def cost_center_options(
        self, *, role: str, organization: str | None = None
    ) -> list[CopilotCostCenterOption]:
        connection = self._connection_for_viewer(organization, role)
        token = self._cipher.decrypt(connection.get("credential_ciphertext"))
        if not token:
            raise CopilotNotConfiguredError("GitHub Copilot credential is unavailable")
        client = self._client_factory(token)
        try:
            centers = await client.enterprise_cost_centers(
                str(connection["organization"])
            )
        finally:
            await client.aclose()
        return sorted(
            [
                CopilotCostCenterOption(
                    id=str(center.get("id") or ""),
                    name=str(center.get("name") or ""),
                    state=(
                        "archived"
                        if str(center.get("state") or "").lower()
                        in {"archived", "deleted"}
                        else "active"
                    ),
                )
                for center in centers
                if center.get("id") and center.get("name")
            ],
            key=lambda center: (center.state != "active", center.name.lower()),
        )

    def cost_center_requests(
        self, user_id: UUID, role: str
    ) -> CopilotCostCenterRequestList:
        rows = self._store.list_cost_center_requests(
            None if role == "owner" else user_id
        )
        items = [_cost_center_request(row) for row in rows]
        return CopilotCostCenterRequestList(
            items=items,
            can_review=role == "owner",
            pending_count=sum(item.status == "pending" for item in items),
            approved_count=sum(item.status == "approved" for item in items),
            rejected_count=sum(item.status == "rejected" for item in items),
        )

    async def create_cost_center_request(
        self,
        write: CopilotCostCenterRequestCreate,
        *,
        user_id: UUID,
        user_email: str,
        user_display_name: str | None,
        role: str,
    ) -> CopilotCostCenterRequest:
        connection = self._connection_for_viewer(write.organization, role)
        identity = self._store.identity(user_id)
        if identity is None:
            raise CopilotIdentityRequiredError(
                "Your application account is not linked to a GitHub login"
            )
        options = await self.cost_center_options(
            role=role, organization=write.organization
        )
        target = next(
            (
                option
                for option in options
                if option.id == write.cost_center_id and option.state == "active"
            ),
            None,
        )
        if target is None:
            raise CopilotNotFoundError("Active GitHub cost center not found")
        row = self._store.create_cost_center_request(
            connection_id=connection["id"],
            organization=connection["organization"],
            app_user_id=user_id,
            user_email=user_email,
            user_display_name=user_display_name,
            github_login=identity["github_login"],
            cost_center_id=target.id,
            cost_center_name=target.name,
            reason=write.reason.strip(),
        )
        return _cost_center_request(row)

    async def review_cost_center_request(
        self,
        request_id: UUID,
        write: CopilotCostCenterRequestReview,
        *,
        actor: str,
    ) -> CopilotCostCenterRequest:
        request = self._store.cost_center_request(request_id)
        if request is None:
            raise CopilotNotFoundError("Cost center request not found")
        claimed = self._store.claim_cost_center_review(request_id, actor)
        if claimed is None:
            raise CopilotStoreConflictError(
                "Cost center request is no longer pending"
            )
        if write.decision == "reject":
            return _cost_center_request(
                self._store.complete_cost_center_review(
                    request_id=request_id,
                    actor=actor,
                    status="rejected",
                    review_comment=write.comment.strip(),
                    github_sync_status="not_requested",
                    github_sync_error=None,
                )
            )
        sync_status = "skipped"
        sync_error: str | None = None
        if write.apply_to_github:
            connection = self._connection(str(claimed["organization"]))
            if not connection.get("write_enabled"):
                sync_status = "failed"
                sync_error = "GitHub writes are disabled for this connection"
            else:
                token = self._cipher.decrypt(connection.get("credential_ciphertext"))
                if not token:
                    sync_status = "failed"
                    sync_error = "GitHub Copilot credential is unavailable"
                else:
                    client = self._client_factory(token)
                    try:
                        centers = await client.enterprise_cost_centers(
                            str(claimed["organization"])
                        )
                        active = any(
                            str(center.get("id") or "") == str(claimed["cost_center_id"])
                            and str(center.get("state") or "active").lower() == "active"
                            for center in centers
                        )
                        if not active:
                            sync_status = "failed"
                            sync_error = "GitHub cost center is no longer active"
                        else:
                            await client.add_cost_center_users(
                                str(claimed["organization"]),
                                str(claimed["cost_center_id"]),
                                [str(claimed["github_login"])],
                            )
                            sync_status = "updated"
                    except GitHubCopilotApiError as error:
                        sync_status = "failed"
                        sync_error = str(error)
                    finally:
                        await client.aclose()
        return _cost_center_request(
            self._store.complete_cost_center_review(
                request_id=request_id,
                actor=actor,
                status="approved",
                review_comment=write.comment.strip(),
                github_sync_status=sync_status,
                github_sync_error=sync_error,
            )
        )

    def budget_requests(self, user_id: UUID, role: str) -> CopilotBudgetRequestList:
        rows = self._store.list_budget_requests(None if role == "owner" else user_id)
        items = [_budget_request(row) for row in rows]
        return CopilotBudgetRequestList(
            items=items,
            can_review=role == "owner",
            pending_count=sum(item.status == "pending" for item in items),
            approved_count=sum(item.status == "approved" for item in items),
            rejected_count=sum(item.status == "rejected" for item in items),
        )

    def create_budget_request(
        self,
        write: CopilotBudgetRequestCreate,
        *,
        user_id: UUID,
        user_email: str,
        user_display_name: str | None,
        role: str,
    ) -> CopilotBudgetRequest:
        connection = self._connection_for_viewer(write.organization, role)
        identity = self._store.identity(user_id)
        if identity is None:
            raise CopilotIdentityRequiredError(
                "Your application account is not linked to a GitHub login"
            )
        row = self._store.create_budget_request(
            connection_id=connection["id"],
            organization=connection["organization"],
            app_user_id=user_id,
            user_email=user_email,
            user_display_name=user_display_name,
            github_login=identity["github_login"],
            requested_amount_usd=write.amount_usd,
            reason=write.reason.strip(),
        )
        return _budget_request(row)

    async def review_budget_request(
        self,
        request_id: UUID,
        write: CopilotBudgetRequestReview,
        *,
        actor: str,
    ) -> CopilotBudgetRequest:
        request = self._store.budget_request(request_id)
        if request is None:
            raise CopilotNotFoundError("Budget request not found")
        claimed = self._store.claim_budget_review(request_id, actor)
        if claimed is None:
            raise CopilotStoreConflictError("Budget request is no longer pending")
        if write.decision == "reject":
            row = self._store.complete_budget_review(
                request_id=request_id,
                actor=actor,
                status="rejected",
                approved_amount_usd=None,
                review_comment=write.comment.strip(),
                github_sync_status="not_requested",
                github_sync_error=None,
            )
            return _budget_request(row)

        amount = write.approved_amount_usd or int(claimed["requested_amount_usd"])
        sync_status = "skipped"
        sync_error: str | None = None
        if write.apply_to_github:
            connection = self._connection(str(claimed["organization"]))
            if not connection.get("write_enabled"):
                sync_status = "failed"
                sync_error = "GitHub writes are disabled for this connection"
            else:
                token = self._cipher.decrypt(connection.get("credential_ciphertext"))
                if not token:
                    sync_status = "failed"
                    sync_error = "GitHub Copilot credential is unavailable"
                else:
                    client = self._client_factory(token)
                    try:
                        budgets = await client.budgets(
                            str(claimed["organization"]),
                            user=str(claimed["github_login"]),
                        )
                        existing = _budget_for_login(
                            budgets, str(claimed["github_login"])
                        )
                        if existing and existing.get("id"):
                            if _integer(existing.get("budget_amount")) != amount:
                                await client.update_user_budget(
                                    str(claimed["organization"]),
                                    str(existing["id"]),
                                    amount,
                                )
                            sync_status = "updated"
                        else:
                            await client.create_user_budget(
                                str(claimed["organization"]),
                                str(claimed["github_login"]),
                                amount,
                            )
                            sync_status = "created"
                    except GitHubCopilotApiError as error:
                        sync_status = "failed"
                        sync_error = str(error)[:500]
                    finally:
                        await client.aclose()
        row = self._store.complete_budget_review(
            request_id=request_id,
            actor=actor,
            status="approved",
            approved_amount_usd=amount,
            review_comment=write.comment.strip(),
            github_sync_status=sync_status,
            github_sync_error=sync_error,
        )
        return _budget_request(row)