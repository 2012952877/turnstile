from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from backend.data_sources.github_copilot.client import GitHubCopilotApiError, GitHubCopilotClient
from backend.data_sources.github_copilot.contracts import (
    CopilotBudgetRequestCreate,
    CopilotBudgetRequestReview,
    CopilotConnectionWrite,
    CopilotCostCenterRequestCreate,
    CopilotCostCenterRequestReview,
    CopilotOAuthConfigWrite,
)
from backend.data_sources.github_copilot.csv_import import parse_copilot_csv
from backend.data_sources.github_copilot.service import CopilotPermissionError, CopilotService
from backend.security import CredentialCipher


def test_report_download_does_not_forward_the_github_token() -> None:
    api_requests: list[httpx.Request] = []
    download_requests: list[httpx.Request] = []

    def api_handler(request: httpx.Request) -> httpx.Response:
        api_requests.append(request)
        return httpx.Response(
            200,
            json={
                "download_links": ["https://reports.example/usage.ndjson?signature=opaque"],
                "report_start_day": "2026-07-01",
                "report_end_day": "2026-07-28",
            },
        )

    def download_handler(request: httpx.Request) -> httpx.Response:
        download_requests.append(request)
        return httpx.Response(
            200,
            content=(
                b'{"day":"2026-07-27","user_login":"alice",'
                b'"user_initiated_interaction_count":2}\n'
                b'{"day":"2026-07-28","user_login":"bob",'
                b'"user_initiated_interaction_count":3}\n'
            ),
        )

    async def exercise() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        async def resolve(_: str) -> list[str]:
            return ["8.8.8.8"]

        client = GitHubCopilotClient(
            "secret-token",
            api_transport=httpx.MockTransport(api_handler),
            download_transport=httpx.MockTransport(download_handler),
            host_resolver=resolve,
        )
        try:
            return await client.usage_report("example-enterprise", "users")
        finally:
            await client.aclose()

    metadata, records = asyncio.run(exercise())

    assert metadata["report_end_day"] == "2026-07-28"
    assert [record["user_login"] for record in records] == ["alice", "bob"]
    assert api_requests[0].headers["authorization"] == "Bearer secret-token"
    assert "authorization" not in download_requests[0].headers


def test_enterprise_report_falls_back_from_the_organization_endpoint() -> None:
    api_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        api_paths.append(request.url.path)
        if request.url.path.startswith("/orgs/"):
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(
            200,
            json={
                "download_links": [],
                "report_start_day": "2026-07-01",
                "report_end_day": "2026-07-28",
            },
        )

    async def exercise() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        client = GitHubCopilotClient(
            "secret-token", api_transport=httpx.MockTransport(handler)
        )
        try:
            return await client.usage_report("example-enterprise", "organization")
        finally:
            await client.aclose()

    metadata, records = asyncio.run(exercise())

    assert metadata["report_end_day"] == "2026-07-28"
    assert records == []
    assert api_paths == [
        "/orgs/example-enterprise/copilot/metrics/reports/organization-28-day/latest",
        "/enterprises/example-enterprise/copilot/metrics/reports/enterprise-28-day/latest",
    ]


def test_report_download_rejects_private_dns_resolution() -> None:
    def api_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"download_links": ["https://reports.example/usage.ndjson"]},
        )

    async def resolve(_: str) -> list[str]:
        return ["127.0.0.1"]

    async def exercise() -> None:
        client = GitHubCopilotClient(
            "secret-token",
            api_transport=httpx.MockTransport(api_handler),
            download_transport=httpx.MockTransport(lambda _: httpx.Response(200)),
            host_resolver=resolve,
        )
        try:
            await client.usage_report("example-enterprise", "users")
        finally:
            await client.aclose()

    try:
        asyncio.run(exercise())
    except GitHubCopilotApiError as error:
        assert error.status_code == 502
        assert str(error) == "GitHub returned an unsafe report host"
    else:
        raise AssertionError("Private report hosts must be rejected")


def test_report_download_rejects_malformed_content_length() -> None:
    def api_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"download_links": ["https://reports.example/usage.ndjson"]},
        )

    def download_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-length": "not-a-number"},
            content=b"{}\n",
        )

    async def resolve(_: str) -> list[str]:
        return ["8.8.8.8"]

    async def exercise() -> None:
        client = GitHubCopilotClient(
            "secret-token",
            api_transport=httpx.MockTransport(api_handler),
            download_transport=httpx.MockTransport(download_handler),
            host_resolver=resolve,
        )
        try:
            await client.usage_report("example-enterprise", "users")
        finally:
            await client.aclose()

    try:
        asyncio.run(exercise())
    except GitHubCopilotApiError as error:
        assert error.status_code == 502
        assert str(error) == "GitHub report has an invalid content length"
    else:
        raise AssertionError("Malformed content lengths must be rejected")


def test_seat_pagination_reads_every_page() -> None:
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        pages.append(page)
        count = 100 if page == 1 else 1
        return httpx.Response(
            200,
            json={
                "total_seats": 101,
                "seats": [
                    {"assignee": {"login": f"person-{page}-{index}"}}
                    for index in range(count)
                ],
            },
        )

    async def exercise() -> list[dict[str, Any]]:
        client = GitHubCopilotClient(
            "secret-token", api_transport=httpx.MockTransport(handler)
        )
        try:
            return await client.seats("example-enterprise")
        finally:
            await client.aclose()

    seats = asyncio.run(exercise())

    assert pages == [1, 2]
    assert len(seats) == 101


def test_enterprise_governance_reads_use_the_official_paginated_paths() -> None:
    requests: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, dict(request.url.params)))
        if request.url.path.endswith("/cost-centers"):
            return httpx.Response(
                200,
                json={
                    "costCenters": [
                        {
                            "id": f"{request.url.params['state']}-1",
                            "name": f"{request.url.params['state']} center",
                            "state": request.url.params["state"],
                        }
                    ]
                },
            )
        if request.url.path.endswith("/budgets"):
            return httpx.Response(
                200,
                json={
                    "budgets": [{"id": "budget-1", "budget_scope": "enterprise"}],
                    "has_next_page": False,
                },
            )
        if request.url.path.endswith("/teams"):
            return httpx.Response(200, json=[{"slug": "ent:platform"}])
        if request.url.path.endswith("/memberships"):
            return httpx.Response(200, json=[{"login": "alice"}])
        if request.url.path == "/orgs/example-org/members":
            return httpx.Response(200, json=[{"login": "bob"}])
        if request.url.path == "/orgs/example-org/teams/platform/members":
            return httpx.Response(200, json=[{"login": "carol"}])
        if request.url.path.endswith("/organizations"):
            return httpx.Response(200, json=[{"login": "example-org"}])
        raise AssertionError(f"Unexpected request: {request.url}")

    async def exercise() -> tuple[list[dict[str, Any]], ...]:
        client = GitHubCopilotClient(
            "secret-token", api_transport=httpx.MockTransport(handler)
        )
        try:
            return (
                await client.enterprise_cost_centers("example-enterprise"),
                await client.enterprise_teams("example-enterprise"),
                await client.enterprise_team_memberships(
                    "example-enterprise", "ent:platform"
                ),
                await client.enterprise_team_organizations(
                    "example-enterprise", "ent:platform"
                ),
                await client.enterprise_organizations("example-enterprise"),
                await client.all_budgets("example-enterprise"),
                await client.organization_members("example-org"),
                await client.organization_team_members("example-org", "platform"),
            )
        finally:
            await client.aclose()

    (
        cost_centers,
        teams,
        members,
        team_orgs,
        enterprise_orgs,
        budgets,
        organization_members,
        organization_team_members,
    ) = asyncio.run(exercise())

    assert [item["state"] for item in cost_centers] == ["active", "deleted"]
    assert teams == [{"slug": "ent:platform"}]
    assert members == [{"login": "alice"}]
    assert team_orgs == [{"login": "example-org"}]
    assert enterprise_orgs == [{"login": "example-org"}]
    assert budgets == [{"id": "budget-1", "budget_scope": "enterprise"}]
    assert organization_members == [{"login": "bob"}]
    assert organization_team_members == [{"login": "carol"}]
    assert requests == [
        (
            "/enterprises/example-enterprise/settings/billing/cost-centers",
            {"page": "1", "per_page": "100", "state": "active"},
        ),
        (
            "/enterprises/example-enterprise/settings/billing/cost-centers",
            {"page": "1", "per_page": "100", "state": "deleted"},
        ),
        (
            "/enterprises/example-enterprise/teams",
            {"page": "1", "per_page": "100"},
        ),
        (
            "/enterprises/example-enterprise/teams/ent:platform/memberships",
            {"page": "1", "per_page": "100"},
        ),
        (
            "/enterprises/example-enterprise/teams/ent:platform/organizations",
            {"page": "1", "per_page": "100"},
        ),
        (
            "/enterprises/example-enterprise/organizations",
            {"page": "1", "per_page": "100"},
        ),
        (
            "/enterprises/example-enterprise/settings/billing/budgets",
            {"page": "1", "per_page": "100"},
        ),
        (
            "/orgs/example-org/members",
            {"page": "1", "per_page": "100"},
        ),
        (
            "/orgs/example-org/teams/platform/members",
            {"page": "1", "per_page": "100"},
        ),
    ]


def test_cost_center_assignment_posts_only_normalized_users() -> None:
    captured: list[tuple[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.url.path, request.read().decode()))
        return httpx.Response(200, json={"status": "ok"})

    async def exercise() -> dict[str, Any]:
        client = GitHubCopilotClient(
            "secret-token", api_transport=httpx.MockTransport(handler)
        )
        try:
            return await client.add_cost_center_users(
                "example-enterprise", "center-1", ["Alice", "alice"]
            )
        finally:
            await client.aclose()

    assert asyncio.run(exercise()) == {"status": "ok"}
    assert captured == [
        (
            "/enterprises/example-enterprise/settings/billing/cost-centers/center-1/resource",
            '{"users":["alice"]}',
        )
    ]


def test_enterprise_cost_centers_accept_an_empty_result() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"costCenters": []})

    async def exercise() -> list[dict[str, Any]]:
        client = GitHubCopilotClient(
            "secret-token", api_transport=httpx.MockTransport(handler)
        )
        try:
            return await client.enterprise_cost_centers("example-enterprise")
        finally:
            await client.aclose()

    assert asyncio.run(exercise()) == []


class StubCopilotStore:
    def __init__(self) -> None:
        self.connection_row: dict[str, Any] = {
            "id": uuid4(),
            "organization": "example-enterprise",
            "display_name": "example-enterprise",
            "credential_ciphertext": None,
            "credential_hint": "...oken",
            "write_enabled": False,
            "is_default": True,
        }
        self.identities: dict[UUID, dict[str, Any]] = {}
        self.users: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.cost_center_requests: list[dict[str, Any]] = []
        self.imports: list[dict[str, Any]] = []
        self.imported_rows: list[dict[str, Any]] = []
        self.oauth_settings: dict[str, dict[str, Any]] = {}
        self.oauth_states: dict[str, dict[str, Any]] = {}

    def list_connections(self) -> Sequence[dict[str, Any]]:
        return [self.connection_row]

    def connection(self, organization: str | None = None) -> dict[str, Any] | None:
        if organization and organization != self.connection_row["organization"]:
            return None
        return self.connection_row

    def save_connection(self, **values: Any) -> dict[str, Any]:
        self.connection_row.update(values)
        return self.connection_row

    def identity(self, app_user_id: UUID) -> dict[str, Any] | None:
        return self.identities.get(app_user_id)

    def list_identities(self) -> Sequence[dict[str, Any]]:
        return self.users

    def save_identity(
        self, app_user_id: UUID, github_login: str, updated_by: str
    ) -> dict[str, Any]:
        del updated_by
        row = {
            "app_user_id": app_user_id,
            "email": "member@example.com",
            "display_name": "Member",
            "github_login": github_login,
        }
        self.identities[app_user_id] = row
        return row

    def oauth_setting(self, origin: str) -> dict[str, Any] | None:
        return self.oauth_settings.get(origin)

    def save_oauth_setting(self, **values: Any) -> dict[str, Any]:
        row = dict(values)
        self.oauth_settings[str(values["origin"])] = row
        return row

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
    ) -> None:
        self.oauth_states[state_sha256] = {
            "app_user_id": app_user_id,
            "origin": origin,
            "return_origin": return_origin,
            "return_path": return_path,
            "purpose": purpose,
            "organization": organization,
            "expires_at": expires_at,
        }

    def consume_oauth_state(
        self, state_sha256: str, origin: str
    ) -> dict[str, Any] | None:
        row = self.oauth_states.get(state_sha256)
        if (
            row is None
            or row["origin"] != origin
            or row["expires_at"] <= datetime.now(UTC)
        ):
            return None
        del self.oauth_states[state_sha256]
        return {
            "app_user_id": row["app_user_id"],
            "email": "member@example.com",
            "return_origin": row["return_origin"],
            "return_path": row["return_path"],
            "purpose": row["purpose"],
            "organization": row["organization"],
            "role": "owner",
        }

    def create_budget_request(self, **values: Any) -> dict[str, Any]:
        now = datetime.now(UTC)
        row = {
            "id": uuid4(),
            **values,
            "approved_amount_usd": None,
            "status": "pending",
            "github_sync_status": "not_requested",
            "github_sync_error": None,
            "reviewed_by": None,
            "review_comment": None,
            "created_at": now,
            "updated_at": now,
            "reviewed_at": None,
        }
        self.requests.append(row)
        return row

    def list_budget_requests(
        self, app_user_id: UUID | None = None
    ) -> Sequence[dict[str, Any]]:
        if app_user_id is None:
            return self.requests
        return [row for row in self.requests if row["app_user_id"] == app_user_id]

    def budget_request(self, request_id: UUID) -> dict[str, Any] | None:
        return next((row for row in self.requests if row["id"] == request_id), None)

    def claim_budget_review(self, request_id: UUID, actor: str) -> dict[str, Any] | None:
        row = self.budget_request(request_id)
        if row is None or row["status"] != "pending":
            return None
        row["processing_by"] = actor
        return row

    def complete_budget_review(self, **values: Any) -> dict[str, Any]:
        row = self.budget_request(values["request_id"])
        assert row is not None
        row.update(
            status=values["status"],
            approved_amount_usd=values["approved_amount_usd"],
            reviewed_by=values["actor"],
            review_comment=values["review_comment"],
            github_sync_status=values["github_sync_status"],
            github_sync_error=values["github_sync_error"],
            reviewed_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        return row

    def save_usage_import(self, **values: Any) -> dict[str, Any]:
        existing = next(
            (
                row
                for row in self.imports
                if row["connection_id"] == values["connection_id"]
                and row["source_kind"] == values["source_kind"]
                and row["content_sha256"] == values["content_sha256"]
            ),
            None,
        )
        if existing is not None:
            return existing
        known = {
            row["row_sha256"]
            for row in self.imported_rows
            if row["connection_id"] == values["connection_id"]
            and row["source_kind"] == values["source_kind"]
        }
        unique_rows: list[dict[str, Any]] = []
        for parsed in values["rows"]:
            if parsed["row_sha256"] in known:
                continue
            known.add(parsed["row_sha256"])
            unique_rows.append(
                {
                    **parsed,
                    "connection_id": values["connection_id"],
                    "source_kind": values["source_kind"],
                }
            )
        now = datetime.now(UTC)
        row = {
            "id": uuid4(),
            "connection_id": values["connection_id"],
            "source_kind": values["source_kind"],
            "filename": values["filename"],
            "content_sha256": values["content_sha256"],
            "row_count": len(values["rows"]),
            "inserted_count": len(unique_rows),
            "duplicate_count": len(values["rows"]) - len(unique_rows),
            "first_usage_date": values["first_usage_date"],
            "last_usage_date": values["last_usage_date"],
            "created_at": now,
        }
        self.imports.append(row)
        self.imported_rows.extend(unique_rows)
        return row

    def usage_imports(
        self, connection_id: UUID, source_kind: str
    ) -> Sequence[dict[str, Any]]:
        return [
            row
            for row in self.imports
            if row["connection_id"] == connection_id
            and row["source_kind"] == source_kind
        ]

    def imported_usage(
        self, connection_id: UUID, source_kind: str
    ) -> dict[str, Any]:
        rows = [
            row
            for row in self.imported_rows
            if row["connection_id"] == connection_id
            and row["source_kind"] == source_kind
        ]
        if not rows:
            return {
                "totals": {},
                "daily": [],
                "primary_breakdown": [],
                "product_breakdown": [],
                "organization_breakdown": [],
                "cost_center_breakdown": [],
                "users": [],
                "filters": {},
            }
        first = min(row["usage_date"] for row in rows)
        last = max(row["usage_date"] for row in rows)
        total_quantity = sum(row["quantity"] for row in rows)
        total_gross = sum(row["gross_amount"] for row in rows)
        total_net = sum(row["net_amount"] for row in rows)
        users = sorted({row["username"] for row in rows})
        organizations = sorted({row["organization"] for row in rows})
        primary_key = "model" if source_kind == "ai_usage" else "sku"
        return {
            "totals": {
                "first_usage_date": first,
                "last_usage_date": last,
                "total_quantity": total_quantity,
                "total_gross_amount": total_gross,
                "total_net_amount": total_net,
                "unique_users": len(users),
                "unique_organizations": len(organizations),
            },
            "daily": [
                {
                    "day": first,
                    "quantity": total_quantity,
                    "gross_amount": total_gross,
                    "net_amount": total_net,
                    "active_users": len(users),
                }
            ],
            "primary_breakdown": [
                {
                    "key": rows[0][primary_key],
                    "quantity": total_quantity,
                    "gross_amount": total_gross,
                    "net_amount": total_net,
                    "user_count": len(users),
                }
            ],
            "product_breakdown": (
                [
                    {
                        "key": rows[0]["product"],
                        "quantity": total_quantity,
                        "gross_amount": total_gross,
                        "net_amount": total_net,
                        "user_count": len(users),
                    }
                ]
                if source_kind == "usage_report"
                else []
            ),
            "organization_breakdown": [],
            "cost_center_breakdown": [],
            "users": [
                {
                    "login": rows[0]["username"],
                    "organization": rows[0]["organization"],
                    "cost_center_name": rows[0].get("cost_center_name"),
                    "quantity": total_quantity,
                    "gross_amount": total_gross,
                    "net_amount": total_net,
                    "active_days": 1,
                    "monthly_quota": rows[0].get("total_monthly_quota"),
                }
            ],
            "filters": {
                "organizations": organizations,
                "cost_centers": [],
                "products": [],
                "skus": [],
            },
        }

    def create_cost_center_request(self, **values: Any) -> dict[str, Any]:
        now = datetime.now(UTC)
        row = {
            "id": uuid4(),
            **values,
            "status": "pending",
            "github_sync_status": "not_requested",
            "github_sync_error": None,
            "reviewed_by": None,
            "review_comment": None,
            "created_at": now,
            "updated_at": now,
            "reviewed_at": None,
        }
        self.cost_center_requests.append(row)
        return row

    def list_cost_center_requests(
        self, app_user_id: UUID | None = None
    ) -> Sequence[dict[str, Any]]:
        if app_user_id is None:
            return self.cost_center_requests
        return [
            row
            for row in self.cost_center_requests
            if row["app_user_id"] == app_user_id
        ]

    def cost_center_request(self, request_id: UUID) -> dict[str, Any] | None:
        return next(
            (row for row in self.cost_center_requests if row["id"] == request_id),
            None,
        )

    def claim_cost_center_review(
        self, request_id: UUID, actor: str
    ) -> dict[str, Any] | None:
        row = self.cost_center_request(request_id)
        if row is None or row["status"] != "pending":
            return None
        row["processing_by"] = actor
        return row

    def complete_cost_center_review(self, **values: Any) -> dict[str, Any]:
        row = self.cost_center_request(values["request_id"])
        assert row is not None
        row.update(
            status=values["status"],
            reviewed_by=values["actor"],
            review_comment=values["review_comment"],
            github_sync_status=values["github_sync_status"],
            github_sync_error=values["github_sync_error"],
            reviewed_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        return row


class StubCopilotApi:
    def __init__(self) -> None:
        self.closed = False
        self.full_seat_reads = 0
        self.member_seat_reads: list[str] = []
        self.ai_users: list[str | None] = []
        self.budget_users: list[str | None] = []
        self.created_budgets = 0
        self.updated_budgets = 0
        self.cost_center_user_writes: list[tuple[str, str, list[str]]] = []

    async def aclose(self) -> None:
        self.closed = True

    async def profile(self) -> dict[str, Any]:
        return {"login": "owner"}

    async def organization(self, organization: str) -> dict[str, Any]:
        return {"login": organization, "name": "Example Enterprise"}

    async def billing(self, organization: str) -> dict[str, Any]:
        assert organization == "example-enterprise"
        return {
            "plan_type": "business",
            "seat_management_setting": "assign_selected",
            "seat_breakdown": {"total": 2, "active_this_cycle": 2},
        }

    @staticmethod
    def _seat(login: str) -> dict[str, Any]:
        return {
            "assignee": {
                "login": login,
                "avatar_url": f"https://avatars.example/{login}",
            },
            "created_at": "2026-07-01T00:00:00Z",
            "last_activity_at": "2026-07-28T12:00:00Z",
            "last_activity_editor": "vscode",
            "plan_type": "business",
        }

    async def seats(self, organization: str) -> list[dict[str, Any]]:
        assert organization == "example-enterprise"
        self.full_seat_reads += 1
        return [self._seat("alice"), self._seat("bob")]

    async def member_seat(self, organization: str, login: str) -> dict[str, Any]:
        assert organization == "example-enterprise"
        self.member_seat_reads.append(login)
        return self._seat(login)

    @staticmethod
    def _user(login: str, interactions: int) -> dict[str, Any]:
        feature = "chat_panel_agent_mode" if login == "alice" else "chat_panel_custom_mode"
        return {
            "day": "2026-07-28",
            "user_login": login,
            "user_initiated_interaction_count": interactions,
            "code_generation_activity_count": interactions * 2,
            "code_acceptance_activity_count": interactions,
            "loc_added_sum": interactions * 10,
            "loc_deleted_sum": interactions,
            "ai_credits_used": float(interactions),
            "ai_adoption_phase": {"phase": "Phase 2"},
            "totals_by_model_feature": [
                {
                    "model": "gpt-5.4",
                    "feature": feature,
                    "user_initiated_interaction_count": interactions,
                    "code_generation_activity_count": interactions * 2,
                    "code_acceptance_activity_count": interactions,
                    "loc_added_sum": interactions * 10,
                    "loc_deleted_sum": interactions,
                }
            ],
            "totals_by_feature": [
                {
                    "feature": feature,
                    "user_initiated_interaction_count": interactions,
                    "code_generation_activity_count": interactions * 2,
                    "code_acceptance_activity_count": interactions,
                    "loc_added_sum": interactions * 10,
                    "loc_deleted_sum": interactions,
                }
            ],
            "totals_by_language_feature": [],
            "totals_by_ide": [],
        }

    async def usage_report(
        self, organization: str, kind: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        assert organization == "example-enterprise"
        metadata = {
            "report_start_day": "2026-07-01",
            "report_end_day": "2026-07-28",
        }
        if kind == "users":
            return metadata, [self._user("alice", 2), self._user("bob", 5)]
        return metadata, [
            {
                "day_totals": [
                    {
                        "day": "2026-07-28",
                        "daily_active_users": 2,
                        "weekly_active_users": 2,
                        "monthly_active_users": 2,
                        "user_initiated_interaction_count": 7,
                        "code_generation_activity_count": 14,
                        "code_acceptance_activity_count": 7,
                        "loc_added_sum": 70,
                        "loc_deleted_sum": 7,
                        "totals_by_model_feature": [],
                        "totals_by_feature": [
                            {
                                "feature": "chat_panel_agent_mode",
                                "user_initiated_interaction_count": 7,
                                "code_generation_activity_count": 14,
                                "code_acceptance_activity_count": 7,
                                "loc_added_sum": 70,
                                "loc_deleted_sum": 7,
                            }
                        ],
                        "totals_by_language_feature": [],
                        "totals_by_ide": [],
                    }
                ]
            }
        ]

    async def ai_credit_usage(
        self,
        organization: str,
        year: int,
        month: int,
        *,
        user: str | None = None,
    ) -> dict[str, Any]:
        assert organization == "example-enterprise"
        assert year >= 2026 and 1 <= month <= 12
        self.ai_users.append(user)
        amount = 2.0 if user == "alice" else 7.0
        return {
            "usageItems": [
                {
                    "model": "gpt-5.4",
                    "grossAmount": amount,
                    "netAmount": amount,
                    "grossQuantity": 10,
                    "discountQuantity": 2,
                    "netQuantity": 8,
                }
            ]
        }

    async def billing_usage(
        self, organization: str, year: int, month: int
    ) -> dict[str, Any]:
        assert organization == "example-enterprise"
        assert year >= 2026 and 1 <= month <= 12
        return {"usageItems": []}

    async def budgets(
        self, organization: str, *, user: str | None = None
    ) -> list[dict[str, Any]]:
        assert organization == "example-enterprise"
        self.budget_users.append(user)
        users = [user] if user else ["alice", "bob"]
        return [
            {
                "id": f"budget-{login}",
                "budget_scope": "user",
                "user": login,
                "budget_amount": 30,
                "consumed_amount": 2 if login == "alice" else 5,
            }
            for login in users
            if login
        ]

    async def all_budgets(self, enterprise: str) -> list[dict[str, Any]]:
        assert enterprise == "example-enterprise"
        return [
            {
                "id": "enterprise-budget",
                "budget_type": "BundlePricing",
                "budget_scope": "enterprise",
                "budget_entity_name": "example-enterprise",
                "budget_product_skus": ["ai_credits"],
                "budget_amount": 1000,
                "consumed_amount": 191.2854,
                "prevent_further_usage": True,
                "budget_alerting": {"will_alert": True, "alert_recipients": []},
            }
        ]

    async def enterprise_cost_centers(
        self, enterprise: str
    ) -> list[dict[str, Any]]:
        assert enterprise == "example-enterprise"
        return [
            {
                "id": "cost-center-1",
                "name": "Platform",
                "state": "active",
                "resources": [{"type": "User", "name": "alice"}],
            }
        ]

    async def enterprise_teams(self, enterprise: str) -> list[dict[str, Any]]:
        assert enterprise == "example-enterprise"
        return [{"slug": "ent:platform", "name": "Platform"}]

    async def enterprise_team_memberships(
        self, enterprise: str, team_slug: str
    ) -> list[dict[str, Any]]:
        assert enterprise == "example-enterprise"
        assert team_slug == "ent:platform"
        return [{"login": "alice"}, {"login": "outside-seat"}]

    async def enterprise_team_organizations(
        self, enterprise: str, team_slug: str
    ) -> list[dict[str, Any]]:
        assert enterprise == "example-enterprise"
        assert team_slug == "ent:platform"
        return [{"login": "example-org"}]

    async def organization_members(
        self, organization: str
    ) -> list[dict[str, Any]]:
        assert organization == "example-org"
        return [{"login": "alice"}]

    async def organization_team_members(
        self, organization: str, team_slug: str
    ) -> list[dict[str, Any]]:
        assert organization == "example-org"
        assert team_slug == "platform"
        return [{"login": "alice"}]

    async def add_cost_center_users(
        self, enterprise: str, cost_center_id: str, users: Sequence[str]
    ) -> dict[str, Any]:
        self.cost_center_user_writes.append(
            (enterprise, cost_center_id, list(users))
        )
        return {"status": "ok"}

    async def create_user_budget(
        self, organization: str, login: str, amount_usd: int
    ) -> dict[str, Any]:
        del organization, login, amount_usd
        self.created_budgets += 1
        return {"budget": {"id": "new-budget"}}

    async def update_user_budget(
        self, organization: str, budget_id: str, amount_usd: int
    ) -> dict[str, Any]:
        del organization, budget_id, amount_usd
        self.updated_budgets += 1
        return {"budget": {"id": "updated-budget"}}


def service_fixture() -> tuple[CopilotService, StubCopilotStore, StubCopilotApi, UUID]:
    store = StubCopilotStore()
    api = StubCopilotApi()
    user_id = uuid4()
    cipher = CredentialCipher(Fernet.generate_key())
    store.connection_row["credential_ciphertext"] = cipher.encrypt("secret-token")
    service = CopilotService(store, cipher, lambda _: api)
    return service, store, api, user_id


def test_member_dashboard_is_trimmed_to_the_explicit_github_identity() -> None:
    service, store, api, user_id = service_fixture()
    store.save_identity(user_id, "alice", "owner@example.com")

    dashboard = asyncio.run(service.dashboard(user_id=user_id, role="member"))

    assert dashboard.viewer_github_login == "alice"
    assert dashboard.can_view_members is False
    assert [member.login for member in dashboard.members] == ["alice"]
    assert dashboard.totals.interactions == 2
    assert dashboard.totals.gross_amount == 2
    assert [item.key for item in dashboard.models] == ["gpt-5.4"]
    assert api.full_seat_reads == 0
    assert api.member_seat_reads == ["alice"]
    assert api.ai_users == ["alice"]
    assert api.budget_users == ["alice"]


def test_member_cannot_select_a_non_default_organization() -> None:
    service, store, _, user_id = service_fixture()
    store.save_identity(user_id, "alice", "owner@example.com")
    store.connection_row["is_default"] = True

    try:
        asyncio.run(
            service.dashboard(
                user_id=user_id,
                role="member",
                organization="another-organization",
            )
        )
    except Exception as error:
        assert str(error) == "Members can use only the default GitHub Copilot organization"
    else:
        raise AssertionError("Members must not select arbitrary configured organizations")


def test_member_has_no_copilot_source_without_an_explicit_default_connection() -> None:
    service, store, _, user_id = service_fixture()
    store.connection_row["is_default"] = False
    store.save_identity(user_id, "alice", "owner@example.com")

    status = service.status(user_id, "member")
    assert status.configured is False
    assert status.connections == []

    try:
        asyncio.run(service.dashboard(user_id=user_id, role="member"))
    except Exception as error:
        assert str(error) == "A default GitHub Copilot organization is not configured"
    else:
        raise AssertionError("Member access requires an explicit default organization")


def test_owner_dashboard_contains_the_organization_members() -> None:
    service, _, api, user_id = service_fixture()

    dashboard = asyncio.run(service.dashboard(user_id=user_id, role="owner"))

    assert dashboard.viewer_github_login is None
    assert dashboard.can_view_members is True
    assert [member.login for member in dashboard.members] == ["alice", "bob"]
    assert dashboard.totals.interactions == 7
    assert dashboard.subscription.estimated_monthly_seat_cost == 38
    assert dashboard.daily[0].active_users == 2
    assert dashboard.daily[0].weekly_active_users == 2
    assert dashboard.daily[0].monthly_active_users == 2
    assert dashboard.daily[0].agent_users == 1
    assert dashboard.daily[0].chat_users == 2
    assert dashboard.features[0].loc_deleted > 0
    assert dashboard.ai_credit_breakdown[0].model == "gpt-5.4"
    assert dashboard.ai_credit_breakdown[0].gross_quantity == 10
    assert dashboard.ai_credit_breakdown[0].discount_quantity == 2
    assert dashboard.ai_credit_breakdown[0].net_quantity == 8
    assert api.full_seat_reads == 1
    assert api.member_seat_reads == []


def test_owner_governance_derives_live_team_cost_center_and_budget_state() -> None:
    service, _, _, _ = service_fixture()

    governance = asyncio.run(service.governance(role="owner"))

    assert [member.login for member in governance.seats] == ["alice", "bob"]
    assert governance.teams[0].member_count == 2
    assert governance.teams[0].seat_count == 1
    assert governance.teams[0].organizations == ["example-org"]
    assert [member.login for member in governance.cost_centers[0].members] == [
        "alice"
    ]
    assert [member.login for member in governance.unassigned_seats] == ["bob"]
    assert governance.budgets[0].scope == "enterprise"
    assert governance.budgets[0].consumed_amount == 191.2854
    assert governance.budgets[0].remaining_amount == 808.7146
    assert governance.budgets[0].prevent_further_usage is True
    assert governance.warnings == []


def test_member_cannot_read_enterprise_governance() -> None:
    service, _, _, _ = service_fixture()

    with pytest.raises(CopilotPermissionError):
        asyncio.run(service.governance(role="member"))


def test_governance_keeps_parent_entities_when_nested_memberships_are_unavailable() -> None:
    service, _, api, _ = service_fixture()

    async def missing_memberships(
        enterprise: str, team_slug: str
    ) -> list[dict[str, Any]]:
        del enterprise, team_slug
        raise GitHubCopilotApiError(404, "Not Found")

    api.enterprise_team_memberships = missing_memberships  # type: ignore[method-assign]

    governance = asyncio.run(service.governance(role="owner"))

    assert [team.slug for team in governance.teams] == ["ent:platform"]
    assert governance.teams[0].members == []
    assert governance.seats
    assert governance.budgets
    assert governance.warnings == [
        "GitHub memberships for enterprise team ent:platform are unavailable"
    ]


def test_usage_import_deduplicates_rows_and_returns_supplemental_aggregate() -> None:
    service, _, _, user_id = service_fixture()
    content = (
        b"date,organization,username,model,quantity,gross_amount\n"
        b"2026-08-17,example-enterprise,Alice,gpt-5.4,2,1.5\n"
        b"2026-08-17,example-enterprise,Alice,gpt-5.4,2,1.5\n"
    )
    parsed = parse_copilot_csv(content)

    summary = service.save_usage_import(
        parsed,
        filename="ai-usage.csv",
        file_size_bytes=len(content),
        user_id=user_id,
        user_email="owner@example.com",
    )
    usage = service.imported_usage("ai_usage")

    assert summary.row_count == 2
    assert summary.inserted_count == 1
    assert summary.duplicate_count == 1
    assert usage.has_data is True
    assert usage.total_quantity == 2
    assert usage.total_gross_amount == 1.5
    assert usage.primary_breakdown[0].key == "gpt-5.4"


def test_usage_report_import_keeps_product_and_sku_breakdowns_separate() -> None:
    service, _, _, user_id = service_fixture()
    content = (
        b"date,organization,username,product,sku,unit_type,quantity,gross_amount,net_amount\n"
        b"2026-08-17,example-enterprise,Alice,Copilot,ai_credits,requests,4,2.5,2\n"
    )
    parsed = parse_copilot_csv(content)

    service.save_usage_import(
        parsed,
        filename="usage-report.csv",
        file_size_bytes=len(content),
        user_id=user_id,
        user_email="owner@example.com",
    )
    usage = service.imported_usage("usage_report")

    assert usage.product_breakdown[0].key == "Copilot"
    assert usage.primary_breakdown[0].key == "ai_credits"
    assert usage.users[0].login == "alice"


def test_member_can_request_an_active_cost_center() -> None:
    service, store, _, user_id = service_fixture()
    store.save_identity(user_id, "alice", "owner@example.com")

    request = asyncio.run(
        service.create_cost_center_request(
            CopilotCostCenterRequestCreate(
                organization="example-enterprise",
                cost_center_id="cost-center-1",
                reason="Platform work",
            ),
            user_id=user_id,
            user_email="member@example.com",
            user_display_name="Member",
            role="member",
        )
    )

    assert request.github_login == "alice"
    assert request.cost_center_name == "Platform"
    assert request.status == "pending"
    assert service.cost_center_requests(user_id, "member").pending_count == 1


def test_cost_center_approval_cannot_write_through_a_read_only_connection() -> None:
    service, store, api, user_id = service_fixture()
    store.save_identity(user_id, "alice", "owner@example.com")
    request = asyncio.run(
        service.create_cost_center_request(
            CopilotCostCenterRequestCreate(
                organization="example-enterprise",
                cost_center_id="cost-center-1",
            ),
            user_id=user_id,
            user_email="member@example.com",
            user_display_name="Member",
            role="member",
        )
    )

    reviewed = asyncio.run(
        service.review_cost_center_request(
            request.id,
            CopilotCostCenterRequestReview(
                decision="approve", apply_to_github=True
            ),
            actor="owner@example.com",
        )
    )

    assert reviewed.status == "approved"
    assert reviewed.github_sync_status == "failed"
    assert reviewed.github_sync_error == "GitHub writes are disabled for this connection"
    assert api.cost_center_user_writes == []


def test_cost_center_approval_writes_only_when_explicitly_enabled() -> None:
    service, store, api, user_id = service_fixture()
    store.connection_row["write_enabled"] = True
    store.save_identity(user_id, "alice", "owner@example.com")
    request = asyncio.run(
        service.create_cost_center_request(
            CopilotCostCenterRequestCreate(
                organization="example-enterprise",
                cost_center_id="cost-center-1",
            ),
            user_id=user_id,
            user_email="member@example.com",
            user_display_name="Member",
            role="member",
        )
    )

    reviewed = asyncio.run(
        service.review_cost_center_request(
            request.id,
            CopilotCostCenterRequestReview(
                decision="approve", apply_to_github=True
            ),
            actor="owner@example.com",
        )
    )

    assert reviewed.github_sync_status == "updated"
    assert reviewed.github_sync_error is None
    assert api.cost_center_user_writes == [
        ("example-enterprise", "cost-center-1", ["alice"])
    ]


def test_owner_dashboard_uses_recent_seat_activity_when_reports_are_unavailable() -> None:
    service, _, api, user_id = service_fixture()
    recent = datetime.now(UTC).isoformat()

    async def unavailable_report(
        organization: str, kind: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        del organization, kind
        raise GitHubCopilotApiError(403, "Metrics policy is disabled")

    api.usage_report = unavailable_report  # type: ignore[method-assign]
    original_seat = api._seat
    api._seat = lambda login: {  # type: ignore[method-assign]
        **original_seat(login),
        "last_activity_at": recent,
    }

    dashboard = asyncio.run(service.dashboard(user_id=user_id, role="owner"))

    assert dashboard.totals.active_users == 2
    assert dashboard.totals.interactions == 0
    assert dashboard.totals.generations == 0


def test_connection_token_is_encrypted_and_not_returned() -> None:
    store = StubCopilotStore()
    api = StubCopilotApi()
    cipher = CredentialCipher(Fernet.generate_key())
    service = CopilotService(store, cipher, lambda _: api)

    summary = asyncio.run(
        service.save_connection(
            CopilotConnectionWrite(
                organization="example-enterprise",
                token=SecretStr("top-secret-token"),
            ),
            "owner@example.com",
        )
    )

    encrypted = store.connection_row["credential_ciphertext"]
    assert isinstance(encrypted, bytes)
    assert encrypted != b"top-secret-token"
    assert cipher.decrypt(encrypted) == "top-secret-token"
    assert "top-secret-token" not in summary.model_dump_json()
    assert summary.credential_hint == "...oken"


def test_enterprise_billing_token_requires_every_read_capability() -> None:
    store = StubCopilotStore()
    api = StubCopilotApi()
    cipher = CredentialCipher(Fernet.generate_key())
    original_ciphertext = cipher.encrypt("oauth-token")
    store.connection_row["credential_ciphertext"] = original_ciphertext

    async def missing_organization(_: str) -> dict[str, Any]:
        raise GitHubCopilotApiError(404, "Not Found")

    api.organization = missing_organization  # type: ignore[assignment]
    service = CopilotService(store, cipher, lambda _: api)

    summary = asyncio.run(
        service.save_connection(
            CopilotConnectionWrite(
                organization="example-enterprise",
                token=SecretStr("enterprise-billing-token"),
            ),
            "owner@example.com",
        )
    )

    assert cipher.decrypt(store.connection_row["credential_ciphertext"]) == (
        "enterprise-billing-token"
    )
    assert summary.credential_hint == "Billing PAT ...oken"
    assert api.full_seat_reads == 1
    assert api.ai_users == [None]
    assert api.budget_users == [None]


def test_failed_enterprise_billing_token_does_not_replace_connection() -> None:
    store = StubCopilotStore()
    api = StubCopilotApi()
    cipher = CredentialCipher(Fernet.generate_key())
    original_ciphertext = cipher.encrypt("oauth-token")
    store.connection_row["credential_ciphertext"] = original_ciphertext

    async def missing_organization(_: str) -> dict[str, Any]:
        raise GitHubCopilotApiError(404, "Not Found")

    async def forbidden_budgets(
        organization: str, *, user: str | None = None
    ) -> list[dict[str, Any]]:
        del organization, user
        raise GitHubCopilotApiError(403, "Forbidden")

    api.organization = missing_organization  # type: ignore[assignment]
    api.budgets = forbidden_budgets  # type: ignore[method-assign]
    service = CopilotService(store, cipher, lambda _: api)

    with pytest.raises(GitHubCopilotApiError, match="Forbidden"):
        asyncio.run(
            service.save_connection(
                CopilotConnectionWrite(
                    organization="example-enterprise",
                    token=SecretStr("incomplete-billing-token"),
                ),
                "owner@example.com",
            )
        )

    assert store.connection_row["credential_ciphertext"] == original_ciphertext


def test_oauth_config_encrypts_secret_and_is_scoped_to_the_site_origin() -> None:
    store = StubCopilotStore()
    cipher = CredentialCipher(Fernet.generate_key())
    service = CopilotService(store, cipher)

    summary = service.save_oauth_config(
        CopilotOAuthConfigWrite(
            client_id="oauth-client-id",
            client_secret=SecretStr("oauth-client-secret"),
        ),
        "https://test.example",
        "owner@example.com",
    )

    row = store.oauth_settings["https://test.example"]
    assert row["client_secret_ciphertext"] != b"oauth-client-secret"
    assert cipher.decrypt(row["client_secret_ciphertext"]) == "oauth-client-secret"
    assert "oauth-client-secret" not in summary.model_dump_json()
    assert summary.effective_callback_url == (
        "https://test.example/api/v1/copilot/oauth/callback"
    )
    assert service.status(uuid4(), "member", "https://test.example").oauth_configured
    assert not service.status(uuid4(), "member", "https://prod.example").oauth_configured


def test_oauth_state_is_hashed_bound_and_uses_a_local_return_path() -> None:
    store = StubCopilotStore()
    cipher = CredentialCipher(Fernet.generate_key())
    service = CopilotService(store, cipher)
    user_id = uuid4()
    service.save_oauth_config(
        CopilotOAuthConfigWrite(
            client_id="oauth-client-id",
            client_secret=SecretStr("oauth-client-secret"),
        ),
        "https://test.example",
        "owner@example.com",
    )

    authorize_url = service.oauth_authorize_url(
        user_id,
        "https://test.example",
        "http://localhost:5173",
        "//attacker.example/redirect",
    )
    query = parse_qs(urlsplit(authorize_url).query)
    state = query["state"][0]
    digest = hashlib.sha256(state.encode()).hexdigest()

    assert state not in store.oauth_states
    assert store.oauth_states[digest]["app_user_id"] == user_id
    assert store.oauth_states[digest]["origin"] == "https://test.example"
    assert store.oauth_states[digest]["return_origin"] == "http://localhost:5173"
    assert store.oauth_states[digest]["return_path"] == (
        "/?page=finops-overview&source=github-copilot"
    )
    assert store.oauth_states[digest]["expires_at"] <= datetime.now(UTC) + timedelta(
        minutes=10, seconds=1
    )


def test_oauth_completion_links_active_member_once_without_persisting_token() -> None:
    store = StubCopilotStore()
    cipher = CredentialCipher(Fernet.generate_key())
    user_id = uuid4()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "transient-user-token"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "Alice"})
        if request.url.path == "/user/memberships/orgs/example-enterprise":
            return httpx.Response(200, json={"state": "active"})
        raise AssertionError(str(request.url))

    service = CopilotService(
        store,
        cipher,
        oauth_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )
    service.save_oauth_config(
        CopilotOAuthConfigWrite(
            client_id="oauth-client-id",
            client_secret=SecretStr("oauth-client-secret"),
        ),
        "https://test.example",
        "owner@example.com",
    )
    authorize_url = service.oauth_authorize_url(
        user_id,
        "https://test.example",
        "http://localhost:5173",
        "/?page=finops-overview&source=github-copilot",
    )
    state = parse_qs(urlsplit(authorize_url).query)["state"][0]

    return_path = asyncio.run(
        service.complete_oauth_link(
            origin="https://test.example",
            state=state,
            code="authorization-code",
        )
    )

    assert return_path == (
        "http://localhost:5173/?page=finops-overview&source=github-copilot"
        "&github_linked=1"
    )
    assert store.identities[user_id]["github_login"] == "alice"
    assert requests[-1].headers["authorization"] == "Bearer transient-user-token"
    assert "transient-user-token" not in repr(store.__dict__)
    with pytest.raises(CopilotPermissionError, match="invalid or expired"):
        asyncio.run(
            service.complete_oauth_link(
                origin="https://test.example",
                state=state,
                code="authorization-code",
            )
        )


def test_oauth_completion_links_enterprise_seat_holder() -> None:
    store = StubCopilotStore()
    api = StubCopilotApi()
    cipher = CredentialCipher(Fernet.generate_key())
    store.connection_row["credential_ciphertext"] = cipher.encrypt("owner-token")
    user_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "member-token"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "enterprise-member"})
        if request.url.path == "/user/memberships/orgs/example-enterprise":
            return httpx.Response(404, json={"message": "Not Found"})
        raise AssertionError(str(request.url))

    service = CopilotService(
        store,
        cipher,
        lambda _: api,
        oauth_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )
    service.save_oauth_config(
        CopilotOAuthConfigWrite(
            client_id="oauth-client-id",
            client_secret=SecretStr("oauth-client-secret"),
        ),
        "https://test.example",
        "owner@example.com",
    )
    authorize_url = service.oauth_authorize_url(
        user_id,
        "https://test.example",
        "http://localhost:5173",
        "/?source=github-copilot",
    )
    state = parse_qs(urlsplit(authorize_url).query)["state"][0]

    return_path = asyncio.run(
        service.complete_oauth_link(
            origin="https://test.example",
            state=state,
            code="authorization-code",
        )
    )

    assert return_path.endswith("&github_linked=1")
    assert store.identities[user_id]["github_login"] == "enterprise-member"
    assert api.member_seat_reads == ["enterprise-member"]


def test_oauth_completion_rejects_inactive_organization_membership() -> None:
    store = StubCopilotStore()
    cipher = CredentialCipher(Fernet.generate_key())
    user_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "transient-user-token"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "outsider"})
        return httpx.Response(200, json={"state": "pending"})

    service = CopilotService(
        store,
        cipher,
        oauth_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )
    service.save_oauth_config(
        CopilotOAuthConfigWrite(
            client_id="oauth-client-id",
            client_secret=SecretStr("oauth-client-secret"),
        ),
        "https://test.example",
        "owner@example.com",
    )
    authorize_url = service.oauth_authorize_url(
        user_id,
        "https://test.example",
        "http://localhost:5173",
        "/?source=github-copilot",
    )
    state = parse_qs(urlsplit(authorize_url).query)["state"][0]

    with pytest.raises(CopilotPermissionError, match="not an active member"):
        asyncio.run(
            service.complete_oauth_link(
                origin="https://test.example",
                state=state,
                code="authorization-code",
            )
        )
    assert user_id not in store.identities


def test_owner_oauth_authorization_creates_encrypted_organization_connection() -> None:
    store = StubCopilotStore()
    cipher = CredentialCipher(Fernet.generate_key())
    user_id = uuid4()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "organization-oauth-token"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "owner-login"})
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[{"login": "example-enterprise"}])
        if request.url.path == "/orgs/example-enterprise":
            return httpx.Response(
                200,
                json={"login": "example-enterprise", "name": "Example Enterprise"},
            )
        if request.url.path == "/orgs/example-enterprise/copilot/billing":
            return httpx.Response(200, json={"plan_type": "business"})
        if request.url.path == "/user/memberships/orgs/example-enterprise":
            return httpx.Response(200, json={"state": "active"})
        if request.url.path == (
            "/orgs/example-enterprise/copilot/metrics/reports/"
            "organization-28-day/latest"
        ):
            return httpx.Response(403, json={"message": "Resource not accessible"})
        raise AssertionError(str(request.url))

    service = CopilotService(
        store,
        cipher,
        oauth_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )
    service.save_oauth_config(
        CopilotOAuthConfigWrite(
            client_id="oauth-client-id",
            client_secret=SecretStr("oauth-client-secret"),
        ),
        "https://test.example",
        "owner@example.com",
    )
    authorize_url = service.oauth_authorize_url(
        user_id,
        "https://test.example",
        "http://localhost:5173",
        "/?page=settings&source=github-copilot",
        "organization",
        "example-enterprise",
    )
    authorize_query = parse_qs(urlsplit(authorize_url).query)
    assert "manage_billing:copilot" in authorize_query["scope"][0].split()
    assert "read:enterprise" not in authorize_query["scope"][0].split()
    assert "read:org" not in authorize_query["scope"][0].split()

    return_path = asyncio.run(
        service.complete_oauth_link(
            origin="https://test.example",
            state=authorize_query["state"][0],
            code="authorization-code",
        )
    )

    assert return_path.endswith("&github_linked=1")
    encrypted = store.connection_row["credential_ciphertext"]
    assert encrypted != b"organization-oauth-token"
    assert cipher.decrypt(encrypted) == "organization-oauth-token"
    assert store.connection_row["organization"] == "example-enterprise"
    assert store.connection_row["display_name"] == "Example Enterprise"
    assert store.connection_row["is_default"] is True
    assert store.connection_row["write_enabled"] is False
    assert any(
        request.url.path == "/orgs/example-enterprise/copilot/billing"
        for request in requests
    )


def test_owner_oauth_authorization_creates_enterprise_connection() -> None:
    store = StubCopilotStore()
    cipher = CredentialCipher(Fernet.generate_key())
    user_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "enterprise-oauth-token"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "enterprise-owner"})
        if request.url.path == "/orgs/example-enterprise":
            return httpx.Response(404, json={"message": "Not Found"})
        if request.url.path == "/enterprises/example-enterprise/copilot/billing/seats":
            return httpx.Response(200, json={"total_seats": 2, "seats": []})
        raise AssertionError(str(request.url))

    service = CopilotService(
        store,
        cipher,
        oauth_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )
    service.save_oauth_config(
        CopilotOAuthConfigWrite(
            client_id="oauth-client-id",
            client_secret=SecretStr("oauth-client-secret"),
        ),
        "https://test.example",
        "owner@example.com",
    )
    authorize_url = service.oauth_authorize_url(
        user_id,
        "https://test.example",
        "http://localhost:5173",
        "/?source=github-copilot",
        "organization",
        "example-enterprise",
    )
    query = parse_qs(urlsplit(authorize_url).query)
    assert "manage_billing:copilot" in query["scope"][0].split()
    assert "read:enterprise" not in query["scope"][0].split()

    asyncio.run(
        service.complete_oauth_link(
            origin="https://test.example",
            state=query["state"][0],
            code="authorization-code",
        )
    )

    assert store.connection_row["organization"] == "example-enterprise"
    assert cipher.decrypt(store.connection_row["credential_ciphertext"]) == (
        "enterprise-oauth-token"
    )


def test_owner_oauth_keeps_metrics_connection_when_billing_probe_is_forbidden() -> None:
    store = StubCopilotStore()
    cipher = CredentialCipher(Fernet.generate_key())
    user_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "metrics-oauth-token"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "owner-login"})
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[{"login": "example-enterprise"}])
        if request.url.path == "/orgs/example-enterprise":
            return httpx.Response(200, json={"login": "example-enterprise"})
        if request.url.path == "/user/memberships/orgs/example-enterprise":
            return httpx.Response(200, json={"state": "active"})
        if request.url.path == "/orgs/example-enterprise/copilot/billing":
            return httpx.Response(403, json={"message": "Resource not accessible"})
        if request.url.path == (
            "/orgs/example-enterprise/copilot/metrics/reports/"
            "organization-28-day/latest"
        ):
            return httpx.Response(200, json={"download_links": []})
        raise AssertionError(str(request.url))

    service = CopilotService(
        store,
        cipher,
        oauth_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )
    service.save_oauth_config(
        CopilotOAuthConfigWrite(
            client_id="oauth-client-id",
            client_secret=SecretStr("oauth-client-secret"),
        ),
        "https://test.example",
        "owner@example.com",
    )
    authorize_url = service.oauth_authorize_url(
        user_id,
        "https://test.example",
        "http://localhost:5173",
        "/?source=github-copilot",
        "organization",
        "example-enterprise",
    )
    query = parse_qs(urlsplit(authorize_url).query)

    asyncio.run(
        service.complete_oauth_link(
            origin="https://test.example",
            state=query["state"][0],
            code="authorization-code",
        )
    )

    assert store.connection_row["organization"] == "example-enterprise"
    assert cipher.decrypt(store.connection_row["credential_ciphertext"]) == (
        "metrics-oauth-token"
    )


def test_budget_approval_does_not_write_to_github_unless_requested() -> None:
    service, store, api, user_id = service_fixture()
    store.save_identity(user_id, "alice", "owner@example.com")
    request = service.create_budget_request(
        CopilotBudgetRequestCreate(
            organization="example-enterprise", amount_usd=30, reason="Project work"
        ),
        user_id=user_id,
        user_email="member@example.com",
        user_display_name="Member",
        role="member",
    )

    reviewed = asyncio.run(
        service.review_budget_request(
            request.id,
            CopilotBudgetRequestReview(decision="approve"),
            actor="owner@example.com",
        )
    )

    assert reviewed.status == "approved"
    assert reviewed.github_sync_status == "skipped"
    assert api.created_budgets == 0
    assert api.updated_budgets == 0


def test_connection_write_flag_blocks_requested_github_mutation() -> None:
    service, store, api, user_id = service_fixture()
    store.save_identity(user_id, "alice", "owner@example.com")
    request = service.create_budget_request(
        CopilotBudgetRequestCreate(organization="example-enterprise", amount_usd=30),
        user_id=user_id,
        user_email="member@example.com",
        user_display_name="Member",
        role="member",
    )

    reviewed = asyncio.run(
        service.review_budget_request(
            request.id,
            CopilotBudgetRequestReview(decision="approve", apply_to_github=True),
            actor="owner@example.com",
        )
    )

    assert reviewed.status == "approved"
    assert reviewed.github_sync_status == "failed"
    assert reviewed.github_sync_error == "GitHub writes are disabled for this connection"
    assert api.created_budgets == 0
    assert api.updated_budgets == 0


def test_budget_retry_does_not_repeat_an_already_applied_github_write() -> None:
    service, store, api, user_id = service_fixture()
    store.connection_row["write_enabled"] = True
    store.save_identity(user_id, "alice", "owner@example.com")
    request = service.create_budget_request(
        CopilotBudgetRequestCreate(organization="example-enterprise", amount_usd=30),
        user_id=user_id,
        user_email="member@example.com",
        user_display_name="Member",
        role="member",
    )

    reviewed = asyncio.run(
        service.review_budget_request(
            request.id,
            CopilotBudgetRequestReview(decision="approve", apply_to_github=True),
            actor="owner@example.com",
        )
    )

    assert reviewed.github_sync_status == "updated"
    assert api.updated_budgets == 0
    assert api.created_budgets == 0