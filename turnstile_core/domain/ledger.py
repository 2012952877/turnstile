from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .models import StrictModel

LedgerScopeType = Literal["person", "application"]
ReservationFinalizationKind = Literal["exact_usage", "terminal_zero", "unverified_upper_bound"]
ReservationFinalizationSource = Literal[
    "apim_gateway_llm_log", "apim_gateway_log", "reservation_timeout"
]


class BudgetReservationFinalization(StrictModel):
    scope_type: LedgerScopeType
    scope_id: str = Field(min_length=1, max_length=255)
    period_start: date
    correlation_id: str = Field(min_length=1, max_length=255)
    reservation_created_at: AwareDatetime
    reservation_tokens: int = Field(ge=0)
    evidence_at: AwareDatetime
    status_code: int | None = Field(default=None, ge=0, le=599)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int = Field(ge=0)
    finalization_kind: ReservationFinalizationKind
    source: ReservationFinalizationSource

    @model_validator(mode="after")
    def validate_evidence(self) -> BudgetReservationFinalization:
        if not self.scope_id.strip() or not self.correlation_id.strip():
            raise ValueError("scope and correlation identifiers must not be blank")
        if self.period_start.day != 1:
            raise ValueError("ledger period must start on the first day of the month")
        if self.evidence_at < self.reservation_created_at:
            raise ValueError("evidence cannot predate the reservation")
        if self.finalization_kind == "exact_usage":
            if (
                self.source != "apim_gateway_llm_log"
                or self.input_tokens is None
                or self.output_tokens is None
                or self.total_tokens != self.input_tokens + self.output_tokens
            ):
                raise ValueError("exact usage requires measured input and output from the LLM log")
        elif self.finalization_kind == "terminal_zero":
            if (
                self.source != "apim_gateway_log"
                or self.status_code is None
                or self.status_code < 400
                or (self.input_tokens, self.output_tokens, self.total_tokens) != (0, 0, 0)
            ):
                raise ValueError("terminal zero requires a failed request with zero usage")
        elif (
            self.source != "reservation_timeout"
            or self.input_tokens is not None
            or self.output_tokens is not None
            or self.status_code is not None
            or self.total_tokens != self.reservation_tokens
        ):
            raise ValueError("an unverified upper bound must retain the full reservation")
        return self
