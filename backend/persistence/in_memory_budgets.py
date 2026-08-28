from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from ..domain.models import TokenUsageRecord
from .repository_support import BudgetConstraintViolation


class InMemoryBudgetRepositoryMixin:
    budget_roll_forward: dict[date, dict[str, Any]]
    department_enforcement: dict[str, dict[str, Any]]
    department_enforcement_audit: list[dict[str, Any]]
    models: list[dict[str, Any]]
    token_budget_audit: list[dict[str, Any]]
    token_budgets: dict[tuple[date, str, str], dict[str, Any]]
    usage_records: list[TokenUsageRecord]
    user_model_access_audit: list[dict[str, Any]]
    user_model_policies: dict[str, dict[str, Any]]

    def list_token_budgets(self, period_start: date) -> list[dict[str, Any]]:
        return [
            row
            for (row_period, _, _), row in self.token_budgets.items()
            if row_period == period_start
        ]

    def token_usage_by_budget_scope(
        self, from_: datetime, to: datetime
    ) -> list[dict[str, Any]]:
        totals: dict[tuple[str, str], int] = {}
        for record in self.usage_records:
            if not from_ <= record.ts < to or record.usage_domain != "apim":
                continue
            tokens = record.input_tokens + record.cached_tokens + record.output_tokens
            for scope_type, scope_id in (
                ("organization", record.organization_id),
                ("department", record.department_id),
                ("user", record.user_id),
            ):
                if scope_id == "unattributed":
                    continue
                key = (scope_type, scope_id)
                totals[key] = totals.get(key, 0) + tokens
        return [
            {"scope_type": scope_type, "scope_id": scope_id, "used_tokens": used_tokens}
            for (scope_type, scope_id), used_tokens in totals.items()
        ]

    def upsert_token_budget(
        self,
        period_start: date,
        scope_type: str,
        scope_id: str,
        parent_scope_id: str | None,
        token_limit: int,
        warning_threshold_percent: int,
        changed_by: str,
    ) -> None:
        key = (period_start, scope_type, scope_id)
        previous = self.token_budgets.get(key)
        if previous is not None and (
            previous["parent_scope_id"] == parent_scope_id
            and previous["token_limit"] == token_limit
            and previous["warning_threshold_percent"] == warning_threshold_percent
        ):
            return
        now = datetime.now(UTC)
        self.token_budgets[key] = {
            "period_start": period_start,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "parent_scope_id": parent_scope_id,
            "token_limit": token_limit,
            "warning_threshold_percent": warning_threshold_percent,
            "updated_at": now,
            "updated_by": changed_by,
        }
        self.token_budget_audit.insert(
            0,
            {
                "id": uuid4(),
                "period_start": period_start,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "action": "assigned" if previous is None else "updated",
                "previous_token_limit": previous["token_limit"] if previous else None,
                "new_token_limit": token_limit,
                "previous_warning_threshold_percent": (
                    previous["warning_threshold_percent"] if previous else None
                ),
                "new_warning_threshold_percent": warning_threshold_percent,
                "changed_at": now,
                "changed_by": changed_by,
            },
        )

    def delete_token_budget(
        self, period_start: date, scope_type: str, scope_id: str, changed_by: str
    ) -> bool:
        previous = self.token_budgets.pop((period_start, scope_type, scope_id), None)
        if previous is None:
            return False
        self.token_budget_audit.insert(
            0,
            {
                "id": uuid4(),
                "period_start": period_start,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "action": "removed",
                "previous_token_limit": previous["token_limit"],
                "new_token_limit": None,
                "previous_warning_threshold_percent": previous[
                    "warning_threshold_percent"
                ],
                "new_warning_threshold_percent": None,
                "changed_at": datetime.now(UTC),
                "changed_by": changed_by,
            },
        )
        return True

    def roll_forward_budgets(
        self, period_start: date, changed_by: str
    ) -> dict[str, Any] | None:
        if period_start in self.budget_roll_forward:
            return None
        occupied = any(period == period_start for period, _, _ in self.token_budgets)
        earlier = [period for period, _, _ in self.token_budgets if period < period_start]
        source_period = None if occupied or not earlier else max(earlier)
        copied = 0
        if source_period is not None:
            for (period, scope_type, scope_id), row in list(self.token_budgets.items()):
                if period != source_period:
                    continue
                self.token_budgets[(period_start, scope_type, scope_id)] = {
                    **row,
                    "period_start": period_start,
                    "updated_at": datetime.now(UTC),
                    "updated_by": changed_by,
                }
                self.token_budget_audit.insert(
                    0,
                    {
                        "id": uuid4(),
                        "period_start": period_start,
                        "scope_type": scope_type,
                        "scope_id": scope_id,
                        "action": "assigned",
                        "previous_token_limit": None,
                        "new_token_limit": row["token_limit"],
                        "previous_warning_threshold_percent": None,
                        "new_warning_threshold_percent": row[
                            "warning_threshold_percent"
                        ],
                        "changed_at": datetime.now(UTC),
                        "changed_by": changed_by,
                    },
                )
                copied += 1
        # Written even when nothing was copied: the marker records that the period has
        # had its one chance to inherit, which is what stops a deliberate removal from
        # being undone on the next tick.
        self.budget_roll_forward[period_start] = {
            "period_start": period_start,
            "source_period_start": source_period,
            "scope_count": copied,
        }
        return dict(self.budget_roll_forward[period_start])

    def list_token_budget_audit(
        self, period_start: date, limit: int
    ) -> list[dict[str, Any]]:
        return [
            row for row in self.token_budget_audit if row["period_start"] == period_start
        ][:limit]

    def list_user_model_policies(
        self, user_ids: Sequence[str]
    ) -> list[dict[str, Any]]:
        return [
            self.user_model_policies[user_id]
            for user_id in user_ids
            if user_id in self.user_model_policies
        ]

    def list_user_model_access_audit(
        self,
        user_ids: Sequence[str],
        from_: datetime,
        to: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        selected = set(user_ids)
        return [
            row
            for row in self.user_model_access_audit
            if row["user_id"] in selected and from_ <= row["changed_at"] < to
        ][:limit]

    def list_department_enforcement(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in sorted(
                self.department_enforcement.values(), key=lambda r: r["department_id"]
            )
        ]

    def set_department_enforcement(
        self, department_id: str, mode: str, changed_by: str
    ) -> bool:
        current = self.department_enforcement.get(department_id)
        previous = None if current is None else str(current["mode"])
        if previous == mode:
            return False
        now = datetime.now(UTC)
        self.department_enforcement[department_id] = {
            "department_id": department_id,
            "mode": mode,
            "updated_at": now,
            "updated_by": changed_by,
        }
        self.department_enforcement_audit.insert(
            0,
            {
                "id": uuid4(),
                "department_id": department_id,
                "previous_mode": previous,
                "new_mode": mode,
                "changed_at": now,
                "changed_by": changed_by,
            },
        )
        return True

    def list_department_enforcement_audit(
        self, from_: datetime, to: datetime, limit: int
    ) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.department_enforcement_audit
            if from_ <= row["changed_at"] < to
        ][:limit]

    def budget_ledger_people(self, period_start: date) -> list[dict[str, Any]]:
        people: list[dict[str, Any]] = []
        active_user_ids: set[str] = set()
        for (period, scope_type, scope_id), budget in sorted(self.token_budgets.items()):
            if period != period_start or scope_type != "user":
                continue
            active_user_ids.add(scope_id)
            department_id = str(budget.get("parent_scope_id") or "")
            enforcement = self.department_enforcement.get(department_id)
            people.append(
                {
                    "user_id": scope_id,
                    "department_id": department_id,
                    "token_limit": int(budget["token_limit"]),
                    "mode": str(enforcement["mode"]) if enforcement else "audit",
                }
            )
        retired_user_ids = {
            str(event["scope_id"])
            for event in self.token_budget_audit
            if event["period_start"] == period_start
            and event["scope_type"] == "user"
            and event["action"] == "removed"
            and str(event["scope_id"]) not in active_user_ids
        }
        people.extend(
            {
                "user_id": user_id,
                "department_id": "",
                "token_limit": 0,
                "mode": "audit",
            }
            for user_id in sorted(retired_user_ids)
        )
        return people

    def budget_ledger_snapshot(
        self, period_start: date, period_end: date
    ) -> list[dict[str, Any]]:
        window_start = datetime.combine(period_start, datetime.min.time(), tzinfo=UTC)
        window_end = datetime.combine(period_end, datetime.min.time(), tzinfo=UTC)
        snapshot: list[dict[str, Any]] = []
        for person in self.budget_ledger_people(period_start):
            scope_id = str(person["user_id"])
            in_period = [
                record
                for record in self.usage_records
                if record.user_id == scope_id
                and record.usage_domain == "apim"
                and window_start <= record.ts < window_end
            ]
            snapshot.append(
                {
                    "user_id": scope_id,
                    "department_id": person["department_id"],
                    "token_limit": int(person["token_limit"]),
                    "confirmed_tokens": sum(
                        record.input_tokens + record.cached_tokens + record.output_tokens
                        for record in in_period
                    ),
                    "mode": person["mode"],
                }
            )
        return snapshot

    def settled_reservation_correlations(
        self, correlation_ids: Sequence[str]
    ) -> set[str]:
        wanted = set(correlation_ids)
        return {
            record.correlation_id
            for record in self.usage_records
            if record.correlation_id in wanted
            and record.usage_domain == "apim"
            and (
                record.status_code >= 400
                or not record.estimated
            )
        }

    def model_access_ledger_snapshot(
        self, user_ids: Sequence[str] | None = None
    ) -> list[dict[str, Any]]:
        wanted = None if user_ids is None else set(user_ids)
        keys = {str(model["id"]): str(model["model_key"]) for model in self.models}
        snapshot: list[dict[str, Any]] = []
        for user_id, policy in sorted(self.user_model_policies.items()):
            if wanted is not None and user_id not in wanted:
                continue
            model_ids = [str(value) for value in policy.get("model_ids", [])]
            snapshot.append(
                {
                    "user_id": user_id,
                    "model_uuids": model_ids,
                    "model_keys": [
                        keys[model_id] for model_id in model_ids if model_id in keys
                    ],
                }
            )
        return snapshot

    def person_budget_state(
        self, user_id: str, period_start: date, period_end: date
    ) -> dict[str, Any] | None:
        budget = self.token_budgets.get((period_start, "user", user_id))
        if budget is None:
            return None
        window_start = datetime.combine(period_start, datetime.min.time(), tzinfo=UTC)
        window_end = datetime.combine(period_end, datetime.min.time(), tzinfo=UTC)
        department_id = str(budget.get("parent_scope_id") or "")
        enforcement = self.department_enforcement.get(department_id)
        in_period = [
            record
            for record in self.usage_records
            if record.user_id == user_id
            and record.usage_domain == "apim"
            and window_start <= record.ts < window_end
        ]
        used_tokens = sum(
            record.input_tokens + record.cached_tokens + record.output_tokens
            for record in in_period
        )
        return {
            "token_limit": int(budget["token_limit"]),
            "department_id": department_id,
            "mode": str(enforcement["mode"]) if enforcement else "audit",
            "used_tokens": used_tokens,
        }

    def bulk_upsert_user_budgets(
        self,
        period_start: date,
        department_id: str,
        entries: Sequence[tuple[str, int]],
        warning_threshold_percent: int,
        changed_by: str,
        *,
        selected_user_ids: Sequence[str] | None = None,
        model_ids: Sequence[UUID] | None = None,
    ) -> None:
        user_ids = list(selected_user_ids or [user_id for user_id, _ in entries])
        normalized_model_ids = (
            list(dict.fromkeys(model_ids)) if model_ids is not None else None
        )
        if entries:
            parent = self.token_budgets.get((period_start, "department", department_id))
            if parent is None:
                raise ValueError(
                    "Assign the department budget before allocating user budgets"
                )
            selected_ids = {user_id for user_id, _ in entries}
            unselected_total = sum(
                int(row["token_limit"])
                for (row_period, scope_type, scope_id), row in self.token_budgets.items()
                if row_period == period_start
                and scope_type == "user"
                and row["parent_scope_id"] == department_id
                and scope_id not in selected_ids
            )
            if unselected_total + sum(limit for _, limit in entries) > int(
                parent["token_limit"]
            ):
                raise BudgetConstraintViolation(
                    "User allocations would exceed the department budget of "
                    f"{int(parent['token_limit'])} tokens"
                )
            for user_id, token_limit in entries:
                self.upsert_token_budget(
                    period_start,
                    "user",
                    user_id,
                    department_id,
                    token_limit,
                    warning_threshold_percent,
                    changed_by,
                )

        if normalized_model_ids is not None:
            requested_models = set(normalized_model_ids)
            enabled_models = {
                model["id"] for model in self.models if model["enabled"]
            }
            if not requested_models.issubset(enabled_models):
                raise BudgetConstraintViolation(
                    "One or more selected models are unavailable"
                )
            now = datetime.now(UTC)
            for user_id in user_ids:
                previous = self.user_model_policies.get(user_id)
                if previous is not None and set(previous["model_ids"]) == set(
                    normalized_model_ids
                ):
                    continue
                self.user_model_policies[user_id] = {
                    "user_id": user_id,
                    "model_ids": normalized_model_ids,
                    "updated_at": now,
                    "updated_by": changed_by,
                }
                self.user_model_access_audit.insert(
                    0,
                    {
                        "id": uuid4(),
                        "user_id": user_id,
                        "previous_model_ids": previous["model_ids"] if previous else [],
                        "new_model_ids": normalized_model_ids,
                        "changed_at": now,
                        "changed_by": changed_by,
                    },
                )
