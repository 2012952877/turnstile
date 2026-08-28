from __future__ import annotations

import ipaddress
import json
import socket
from asyncio import get_running_loop
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Literal
from urllib.parse import quote, urlsplit

import httpx

GITHUB_API_VERSION = "2026-03-10"
GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_API_TIMEOUT_SECONDS = 30
GITHUB_REPORT_DOWNLOAD_TIMEOUT_SECONDS = 60
COPILOT_REPORT_WINDOW_DAYS = 28


class GitHubCopilotApiError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class GitHubCopilotClient:
    API_VERSION = GITHUB_API_VERSION
    MAX_DOWNLOAD_LINKS = 20
    MAX_REPORT_BYTES = 20 * 1024 * 1024
    MAX_REPORT_RECORDS = 50_000

    def __init__(
        self,
        token: str,
        *,
        base_url: str = GITHUB_API_BASE_URL,
        api_transport: httpx.AsyncBaseTransport | None = None,
        download_transport: httpx.AsyncBaseTransport | None = None,
        host_resolver: Callable[[str], Awaitable[Sequence[str]]] | None = None,
    ) -> None:
        self._api = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "Turnstile-FinOps",
                "X-GitHub-Api-Version": self.API_VERSION,
            },
            timeout=GITHUB_API_TIMEOUT_SECONDS,
            transport=api_transport,
        )
        # Signed report URLs are capabilities in their own right. They deliberately use a
        # separate client so the organization token can never follow them to another host.
        self._downloads = httpx.AsyncClient(
            headers={"User-Agent": "Turnstile-FinOps"},
            timeout=GITHUB_REPORT_DOWNLOAD_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
            transport=download_transport,
        )
        self._host_resolver = host_resolver or self._resolve_host

    async def __aenter__(self) -> GitHubCopilotClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._api.aclose()
        await self._downloads.aclose()

    @staticmethod
    def _name(value: str) -> str:
        return quote(value.strip(), safe="")

    @staticmethod
    def _safe_error(response: httpx.Response) -> str:
        try:
            value = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return "GitHub rejected the request"
        if isinstance(value, Mapping) and isinstance(value.get("message"), str):
            return str(value["message"])[:500]
        return "GitHub rejected the request"

    async def _json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._api.request(method, path, params=params, json=body)
        except httpx.RequestError as error:
            raise GitHubCopilotApiError(503, "GitHub is currently unreachable") from error
        if not response.is_success:
            raise GitHubCopilotApiError(response.status_code, self._safe_error(response))
        if response.status_code == 204 or not response.content:
            return {}
        try:
            value = response.json()
        except json.JSONDecodeError as error:
            raise GitHubCopilotApiError(502, "GitHub returned an invalid JSON response") from error
        if not isinstance(value, dict):
            raise GitHubCopilotApiError(502, "GitHub returned an unexpected response shape")
        return value

    async def _json_list(
        self,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
    ) -> list[dict[str, Any]]:
        try:
            response = await self._api.get(path, params=params)
        except httpx.RequestError as error:
            raise GitHubCopilotApiError(503, "GitHub is currently unreachable") from error
        if not response.is_success:
            raise GitHubCopilotApiError(response.status_code, self._safe_error(response))
        try:
            value = response.json()
        except json.JSONDecodeError as error:
            raise GitHubCopilotApiError(502, "GitHub returned invalid JSON") from error
        if not isinstance(value, list):
            raise GitHubCopilotApiError(502, "GitHub returned an unexpected response shape")
        return [item for item in value if isinstance(item, dict)]

    async def _paginated_list(self, path: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = await self._json_list(
                path,
                params={"page": page, "per_page": 100},
            )
            rows.extend(batch)
            if len(batch) < 100:
                return rows
            page += 1

    async def profile(self) -> dict[str, Any]:
        return await self._json("GET", "/user")

    async def organization(self, organization: str) -> dict[str, Any]:
        return await self._json("GET", f"/orgs/{self._name(organization)}")

    async def billing(self, organization: str) -> dict[str, Any]:
        try:
            return await self._json(
                "GET", f"/orgs/{self._name(organization)}/copilot/billing"
            )
        except GitHubCopilotApiError as error:
            if error.status_code != 404:
                raise
        seats = await self.seats(organization)
        plan_type = next(
            (
                str(seat.get("plan_type") or "").lower()
                for seat in seats
                if str(seat.get("plan_type") or "").lower() not in {"", "unknown"}
            ),
            "enterprise",
        )
        return {
            "plan_type": plan_type,
            "seat_breakdown": {"total": len(seats)},
        }

    async def seats(self, organization: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = 1
        account_kind = "orgs"
        while True:
            try:
                payload = await self._json(
                    "GET",
                    f"/{account_kind}/{self._name(organization)}/copilot/billing/seats",
                    params={"page": page, "per_page": 100},
                )
            except GitHubCopilotApiError as error:
                if page != 1 or account_kind != "orgs" or error.status_code != 404:
                    raise
                account_kind = "enterprises"
                continue
            batch = payload.get("seats")
            if not isinstance(batch, list):
                raise GitHubCopilotApiError(502, "GitHub returned an invalid seat list")
            rows.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < 100:
                unique: list[dict[str, Any]] = []
                seen_logins: set[str] = set()
                for row in rows:
                    assignee = row.get("assignee")
                    login = (
                        str(assignee.get("login") or "").lower()
                        if isinstance(assignee, Mapping)
                        else ""
                    )
                    if login and login in seen_logins:
                        continue
                    if login:
                        seen_logins.add(login)
                    unique.append(row)
                return unique
            page += 1

    async def member_seat(self, organization: str, login: str) -> dict[str, Any]:
        try:
            return await self._json(
                "GET",
                f"/orgs/{self._name(organization)}/members/"
                f"{self._name(login)}/copilot",
            )
        except GitHubCopilotApiError as error:
            if error.status_code != 404:
                raise
        return await self._json(
            "GET",
            f"/enterprises/{self._name(organization)}/members/"
            f"{self._name(login)}/copilot",
        )

    @staticmethod
    def _legacy_usage_record(record: Mapping[str, Any]) -> dict[str, Any]:
        # TODO: 疑似废弃补丁，待确认：只有受支持的 organization/enterprise API 均不再
        # 返回 classic `copilot_*` usage shape 时，才能移除此转换与 fallback 测试。
        completions = record.get("copilot_ide_code_completions")
        ide_chat = record.get("copilot_ide_chat")
        dotcom_chat = record.get("copilot_dotcom_chat")
        pull_requests = record.get("copilot_dotcom_pull_requests")
        models: dict[str, dict[str, Any]] = {}
        languages: dict[str, dict[str, Any]] = {}
        ides: dict[str, dict[str, Any]] = {}

        def totals(target: dict[str, dict[str, Any]], key: str) -> dict[str, Any]:
            return target.setdefault(
                key,
                {
                    "interactions": 0,
                    "generations": 0,
                    "acceptances": 0,
                },
            )

        if isinstance(completions, Mapping):
            editors = completions.get("editors")
            for editor in editors if isinstance(editors, list) else []:
                if not isinstance(editor, Mapping):
                    continue
                editor_name = str(editor.get("name") or "Unknown")
                editor_totals = totals(ides, editor_name)
                editor_models = editor.get("models")
                for model in editor_models if isinstance(editor_models, list) else []:
                    if not isinstance(model, Mapping):
                        continue
                    model_name = str(model.get("name") or "Unknown")
                    model_totals = totals(models, model_name)
                    model_languages = model.get("languages")
                    for language in (
                        model_languages if isinstance(model_languages, list) else []
                    ):
                        if not isinstance(language, Mapping):
                            continue
                        suggestions = int(language.get("total_code_suggestions") or 0)
                        acceptances = int(language.get("total_code_acceptances") or 0)
                        model_totals["generations"] += suggestions
                        model_totals["acceptances"] += acceptances
                        editor_totals["generations"] += suggestions
                        editor_totals["acceptances"] += acceptances
                        language_totals = totals(
                            languages, str(language.get("name") or "Unknown")
                        )
                        language_totals["generations"] += suggestions
                        language_totals["acceptances"] += acceptances

        chat_interactions = 0
        if isinstance(ide_chat, Mapping):
            editors = ide_chat.get("editors")
            for editor in editors if isinstance(editors, list) else []:
                if not isinstance(editor, Mapping):
                    continue
                editor_totals = totals(ides, str(editor.get("name") or "Unknown"))
                editor_models = editor.get("models")
                for model in editor_models if isinstance(editor_models, list) else []:
                    if not isinstance(model, Mapping):
                        continue
                    chats = int(model.get("total_chats") or 0)
                    chat_interactions += chats
                    editor_totals["interactions"] += chats
                    totals(models, str(model.get("name") or "Unknown"))[
                        "interactions"
                    ] += chats
        if isinstance(dotcom_chat, Mapping):
            dotcom_models = dotcom_chat.get("models")
            for model in dotcom_models if isinstance(dotcom_models, list) else []:
                if not isinstance(model, Mapping):
                    continue
                chats = int(model.get("total_chats") or 0)
                chat_interactions += chats
                totals(models, str(model.get("name") or "Unknown"))[
                    "interactions"
                ] += chats
        if isinstance(pull_requests, Mapping):
            repositories = pull_requests.get("repositories")
            for repository in repositories if isinstance(repositories, list) else []:
                if not isinstance(repository, Mapping):
                    continue
                repository_models = repository.get("models")
                for model in (
                    repository_models if isinstance(repository_models, list) else []
                ):
                    if not isinstance(model, Mapping):
                        continue
                    summaries = int(model.get("total_pr_summaries_created") or 0)
                    chat_interactions += summaries
                    totals(models, str(model.get("name") or "Unknown"))[
                        "interactions"
                    ] += summaries

        generations = sum(value["generations"] for value in models.values())
        acceptances = sum(value["acceptances"] for value in models.values())

        def breakdown(
            values: Mapping[str, Mapping[str, Any]], key_field: str
        ) -> list[dict[str, Any]]:
            return [
                {
                    key_field: key,
                    "user_initiated_interaction_count": value["interactions"],
                    "code_generation_activity_count": value["generations"],
                    "code_acceptance_activity_count": value["acceptances"],
                }
                for key, value in values.items()
            ]

        return {
            "day": record.get("date"),
            "daily_active_users": record.get("total_active_users", 0),
            "user_initiated_interaction_count": chat_interactions,
            "code_generation_activity_count": generations,
            "code_acceptance_activity_count": acceptances,
            "totals_by_model_feature": breakdown(models, "model"),
            "totals_by_language_feature": breakdown(languages, "language"),
            "totals_by_ide": breakdown(ides, "ide"),
            "totals_by_feature": [
                {
                    "feature": "code_completion",
                    "code_generation_activity_count": generations,
                    "code_acceptance_activity_count": acceptances,
                },
                {
                    "feature": "chat_and_pull_requests",
                    "user_initiated_interaction_count": chat_interactions,
                },
            ],
        }

    async def _legacy_metrics(
        self, organization: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        try:
            raw_records = await self._json_list(
                f"/orgs/{self._name(organization)}/copilot/metrics"
            )
        except GitHubCopilotApiError as error:
            if error.status_code != 404:
                raise
            raw_records = await self._json_list(
                f"/enterprises/{self._name(organization)}/copilot/metrics"
            )
        records = [self._legacy_usage_record(record) for record in raw_records]
        days = sorted(
            str(record["day"])
            for record in records
            if isinstance(record.get("day"), str)
        )
        return {
            "report_start_day": days[0] if days else None,
            "report_end_day": days[-1] if days else None,
            "legacy_metrics": True,
        }, records

    @staticmethod
    def _safe_download_url(value: object) -> tuple[str, str]:
        if not isinstance(value, str):
            raise GitHubCopilotApiError(502, "GitHub returned an invalid report URL")
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise GitHubCopilotApiError(502, "GitHub returned an unsafe report URL")
        hostname = parsed.hostname.lower()
        if hostname == "localhost" or hostname.endswith(".localhost"):
            raise GitHubCopilotApiError(502, "GitHub returned an unsafe report URL")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return value, hostname
        if not address.is_global:
            raise GitHubCopilotApiError(502, "GitHub returned an unsafe report URL")
        return value, hostname

    @staticmethod
    async def _resolve_host(hostname: str) -> Sequence[str]:
        try:
            answers = await get_running_loop().getaddrinfo(
                hostname,
                443,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as error:
            raise GitHubCopilotApiError(502, "GitHub report host cannot be resolved") from error
        return [str(answer[4][0]) for answer in answers]

    async def _require_public_host(self, hostname: str) -> None:
        addresses = set(await self._host_resolver(hostname))
        try:
            parsed = [
                ipaddress.ip_address(address.split("%", 1)[0]) for address in addresses
            ]
        except ValueError as error:
            raise GitHubCopilotApiError(
                502, "GitHub returned an unsafe report host"
            ) from error
        if not parsed or any(not address.is_global for address in parsed):
            raise GitHubCopilotApiError(502, "GitHub returned an unsafe report host")

    @classmethod
    def _parse_report(cls, content: bytes) -> list[dict[str, Any]]:
        if len(content) > cls.MAX_REPORT_BYTES:
            raise GitHubCopilotApiError(502, "GitHub report exceeds the supported size")
        try:
            text = content.decode("utf-8").strip()
        except UnicodeDecodeError as error:
            raise GitHubCopilotApiError(502, "GitHub report is not UTF-8") from error
        if not text:
            return []
        try:
            if text.startswith("["):
                decoded = json.loads(text)
                values = decoded if isinstance(decoded, list) else []
            else:
                values = [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError as error:
            raise GitHubCopilotApiError(502, "GitHub report contains invalid JSON") from error
        rows = [value for value in values if isinstance(value, dict)]
        if len(rows) > cls.MAX_REPORT_RECORDS:
            raise GitHubCopilotApiError(502, "GitHub report contains too many records")
        return rows

    async def usage_report(
        self,
        organization: str,
        kind: Literal["organization", "users"],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        try:
            payload = await self._json(
                "GET",
                f"/orgs/{self._name(organization)}/copilot/metrics/reports/"
                f"{kind}-{COPILOT_REPORT_WINDOW_DAYS}-day/latest",
            )
        except GitHubCopilotApiError as error:
            if error.status_code != 404:
                raise
            enterprise_kind = "enterprise" if kind == "organization" else kind
            try:
                payload = await self._json(
                    "GET",
                    f"/enterprises/{self._name(organization)}/copilot/metrics/reports/"
                    f"{enterprise_kind}-{COPILOT_REPORT_WINDOW_DAYS}-day/latest",
                )
            except GitHubCopilotApiError as enterprise_error:
                if kind != "organization" or enterprise_error.status_code not in {
                    403,
                    404,
                }:
                    raise
                return await self._legacy_metrics(organization)
        links = payload.get("download_links")
        if not isinstance(links, list) or len(links) > self.MAX_DOWNLOAD_LINKS:
            raise GitHubCopilotApiError(502, "GitHub returned an invalid report link list")
        rows: list[dict[str, Any]] = []
        total_bytes = 0
        for raw_url in links:
            url, hostname = self._safe_download_url(raw_url)
            await self._require_public_host(hostname)
            try:
                async with self._downloads.stream("GET", url) as response:
                    if not response.is_success:
                        raise GitHubCopilotApiError(
                            response.status_code, "GitHub report download failed"
                        )
                    declared = response.headers.get("content-length")
                    if declared:
                        try:
                            declared_bytes = int(declared)
                        except ValueError as error:
                            raise GitHubCopilotApiError(
                                502, "GitHub report has an invalid content length"
                            ) from error
                        if declared_bytes < 0:
                            raise GitHubCopilotApiError(
                                502, "GitHub report has an invalid content length"
                            )
                        if total_bytes + declared_bytes > self.MAX_REPORT_BYTES:
                            raise GitHubCopilotApiError(
                                502, "GitHub reports exceed the supported size"
                            )
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        total_bytes += len(chunk)
                        if total_bytes > self.MAX_REPORT_BYTES:
                            raise GitHubCopilotApiError(
                                502, "GitHub reports exceed the supported size"
                            )
                        content.extend(chunk)
            except httpx.RequestError as error:
                raise GitHubCopilotApiError(503, "GitHub report download failed") from error
            rows.extend(self._parse_report(bytes(content)))
            if len(rows) > self.MAX_REPORT_RECORDS:
                raise GitHubCopilotApiError(502, "GitHub reports contain too many records")
        return payload, rows

    async def ai_credit_usage(
        self,
        organization: str,
        year: int,
        month: int,
        *,
        user: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str | int] = {"year": year, "month": month}
        if user:
            params["user"] = user
        try:
            return await self._json(
                "GET",
                f"/organizations/{self._name(organization)}/settings/billing/"
                "ai_credit/usage",
                params=params,
            )
        except GitHubCopilotApiError as error:
            if error.status_code != 404:
                raise
        return await self._json(
            "GET",
            f"/enterprises/{self._name(organization)}/settings/billing/ai_credit/usage",
            params=params,
        )

    async def billing_usage(
        self,
        organization: str,
        year: int,
        month: int,
    ) -> dict[str, Any]:
        params: dict[str, str | int] = {
            "year": year,
            "month": month,
            "product": "copilot",
        }
        try:
            return await self._json(
                "GET",
                f"/organizations/{self._name(organization)}/settings/billing/usage/summary",
                params=params,
            )
        except GitHubCopilotApiError as error:
            if error.status_code != 404:
                raise
        return await self._json(
            "GET",
            f"/enterprises/{self._name(organization)}/settings/billing/usage/summary",
            params=params,
        )

    async def budgets(
        self,
        organization: str,
        *,
        user: str | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = 1
        account_kind = "organizations"
        while True:
            params: dict[str, str | int] = {"page": page, "per_page": 100, "scope": "user"}
            if user:
                params["user"] = user
            try:
                payload = await self._json(
                    "GET",
                    f"/{account_kind}/{self._name(organization)}/settings/billing/budgets",
                    params=params,
                )
            except GitHubCopilotApiError as error:
                if (
                    page != 1
                    or account_kind != "organizations"
                    or error.status_code != 404
                ):
                    raise
                account_kind = "enterprises"
                continue
            batch = payload.get("budgets")
            if not isinstance(batch, list):
                raise GitHubCopilotApiError(502, "GitHub returned an invalid budget list")
            rows.extend(item for item in batch if isinstance(item, dict))
            if not payload.get("has_next_page") or not batch:
                return rows
            page += 1

    async def all_budgets(self, enterprise: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = await self._json(
                "GET",
                f"/enterprises/{self._name(enterprise)}/settings/billing/budgets",
                params={"page": page, "per_page": 100},
            )
            batch = payload.get("budgets")
            if not isinstance(batch, list):
                raise GitHubCopilotApiError(502, "GitHub returned an invalid budget list")
            rows.extend(item for item in batch if isinstance(item, dict))
            if not payload.get("has_next_page") or not batch:
                return rows
            page += 1

    async def enterprise_cost_centers(
        self, enterprise: str
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        path = (
            f"/enterprises/{self._name(enterprise)}/settings/billing/cost-centers"
        )
        for state in ("active", "deleted"):
            page = 1
            while True:
                payload = await self._json(
                    "GET",
                    path,
                    params={"page": page, "per_page": 100, "state": state},
                )
                raw_batch = payload.get("costCenters")
                if raw_batch is None:
                    raw_batch = payload.get("cost_centers")
                if not isinstance(raw_batch, list):
                    raise GitHubCopilotApiError(
                        502, "GitHub returned an invalid cost center list"
                    )
                batch = [item for item in raw_batch if isinstance(item, dict)]
                for item in batch:
                    item_id = str(item.get("id") or "")
                    if item_id and item_id in seen_ids:
                        continue
                    if item_id:
                        seen_ids.add(item_id)
                    rows.append(item)
                if len(raw_batch) < 100:
                    break
                page += 1
        return rows

    async def enterprise_organizations(
        self, enterprise: str
    ) -> list[dict[str, Any]]:
        return await self._paginated_list(
            f"/enterprises/{self._name(enterprise)}/organizations"
        )

    async def enterprise_teams(self, enterprise: str) -> list[dict[str, Any]]:
        return await self._paginated_list(
            f"/enterprises/{self._name(enterprise)}/teams"
        )

    async def enterprise_team_memberships(
        self, enterprise: str, team_slug: str
    ) -> list[dict[str, Any]]:
        return await self._paginated_list(
            f"/enterprises/{self._name(enterprise)}/teams/"
            f"{self._name(team_slug)}/memberships"
        )

    async def enterprise_team_organizations(
        self, enterprise: str, team_slug: str
    ) -> list[dict[str, Any]]:
        return await self._paginated_list(
            f"/enterprises/{self._name(enterprise)}/teams/"
            f"{self._name(team_slug)}/organizations"
        )

    async def organization_members(
        self, organization: str
    ) -> list[dict[str, Any]]:
        return await self._paginated_list(
            f"/orgs/{self._name(organization)}/members"
        )

    async def organization_team_members(
        self, organization: str, team_slug: str
    ) -> list[dict[str, Any]]:
        return await self._paginated_list(
            f"/orgs/{self._name(organization)}/teams/"
            f"{self._name(team_slug)}/members"
        )

    async def add_cost_center_users(
        self, enterprise: str, cost_center_id: str, users: Sequence[str]
    ) -> dict[str, Any]:
        normalized_users = sorted(
            {user.strip().lower() for user in users if user.strip()}
        )
        if not normalized_users:
            raise ValueError("At least one GitHub user is required")
        return await self._json(
            "POST",
            f"/enterprises/{self._name(enterprise)}/settings/billing/"
            f"cost-centers/{self._name(cost_center_id)}/resource",
            body={"users": normalized_users},
        )

    async def create_user_budget(
        self, organization: str, login: str, amount_usd: int
    ) -> dict[str, Any]:
        return await self._json(
            "POST",
            f"/organizations/{self._name(organization)}/settings/billing/budgets",
            body={
                "budget_amount": amount_usd,
                "prevent_further_usage": True,
                "budget_scope": "user",
                "budget_entity_name": "",
                "budget_type": "BundlePricing",
                "budget_product_sku": "ai_credits",
                "budget_alerting": {"will_alert": False, "alert_recipients": []},
                "user": login,
            },
        )

    async def update_user_budget(
        self, organization: str, budget_id: str, amount_usd: int
    ) -> dict[str, Any]:
        return await self._json(
            "PATCH",
            f"/organizations/{self._name(organization)}/settings/billing/budgets/{self._name(budget_id)}",
            body={"budget_amount": amount_usd, "prevent_further_usage": True},
        )


def total(values: Sequence[Mapping[str, Any]], field: str) -> float:
    result = 0.0
    for value in values:
        raw = value.get(field, 0)
        if isinstance(raw, int | float):
            result += float(raw)
    return result