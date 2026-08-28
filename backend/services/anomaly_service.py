from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from ..domain.models import AnomalyRule, AnomalyRuleListResponse, AnomalyRuleWrite
from ..persistence.repository import QueryRepository


class AnomalyRuleConflictError(ValueError):
    pass


class AnomalyRuleNotFoundError(ValueError):
    pass


class AnomalyRuleService:
    def __init__(self, repository: QueryRepository) -> None:
        self._repository = repository

    def list(self) -> AnomalyRuleListResponse:
        return AnomalyRuleListResponse(
            items=[
                AnomalyRule.model_validate(row)
                for row in self._repository.list_anomaly_rules()
            ]
        )

    def create(self, write: AnomalyRuleWrite, changed_by: str) -> AnomalyRule:
        values = self._values(write, changed_by)
        return AnomalyRule.model_validate(self._repository.create_anomaly_rule(values))

    def update(
        self, rule_id: UUID, write: AnomalyRuleWrite, changed_by: str
    ) -> AnomalyRule:
        values = self._values(write, changed_by)
        row = self._repository.update_anomaly_rule(rule_id, values)
        if row is None:
            raise AnomalyRuleNotFoundError("Anomaly rule not found")
        return AnomalyRule.model_validate(row)

    def remove(self, rule_id: UUID) -> None:
        if not self._repository.delete_anomaly_rule(rule_id):
            raise AnomalyRuleNotFoundError("Anomaly rule not found")

    @staticmethod
    def _values(write: AnomalyRuleWrite, changed_by: str) -> Mapping[str, Any]:
        values = write.model_dump()
        values["name"] = write.name.strip()
        values["description"] = write.description.strip()
        values["scope_id"] = write.scope_id.strip() if write.scope_id else None
        values["updated_by"] = changed_by
        if not values["name"]:
            raise AnomalyRuleConflictError("Rule name cannot be blank")
        if write.scope_type == "global":
            values["scope_id"] = None
        elif not values["scope_id"]:
            raise AnomalyRuleConflictError("A non-global rule requires a scope ID")
        if write.threshold_mode == "percentile" and write.threshold_value > 100:
            raise AnomalyRuleConflictError("Percentile threshold must be between 1 and 100")
        if (
            write.metric in {"error_rate_percent", "request_latency_ms"}
            and write.threshold_mode != "absolute"
        ):
            raise AnomalyRuleConflictError(
                f"{write.metric} only supports an absolute threshold"
            )
        if write.metric == "error_rate_percent" and write.threshold_value > 100:
            raise AnomalyRuleConflictError("Error-rate threshold cannot exceed 100%")
        if (
            write.metric == "agent_request_count"
            and write.threshold_mode == "absolute"
            and not write.threshold_value.is_integer()
        ):
            raise AnomalyRuleConflictError(
                "Absolute Agent request threshold must be a whole number"
            )
        return values