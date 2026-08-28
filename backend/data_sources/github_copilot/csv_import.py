from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

MAX_CSV_BYTES = 5 * 1024 * 1024
MAX_CSV_ROWS = 25_000
MAX_CSV_COLUMNS = 30

CsvSourceKind = Literal["ai_usage", "usage_report"]


class CopilotCsvError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedCopilotCsv:
    source_kind: CsvSourceKind
    content_sha256: str
    rows: list[dict[str, Any]]
    first_usage_date: date
    last_usage_date: date


def _required(row: Mapping[str, str], field: str, line: int) -> str:
    value = (row.get(field) or "").strip()
    if not value:
        raise CopilotCsvError(f"CSV row {line} is missing {field}")
    return value


def _date(value: str, line: int) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise CopilotCsvError(
            f"CSV row {line} has an invalid date; expected YYYY-MM-DD"
        ) from error


def _decimal(
    row: Mapping[str, str], field: str, line: int, *, required: bool = False
) -> Decimal | None:
    raw = (row.get(field) or "").strip().replace(",", "")
    if not raw:
        if required:
            raise CopilotCsvError(f"CSV row {line} is missing {field}")
        return None
    if raw.startswith("$"):
        raw = raw[1:]
    try:
        value = Decimal(raw)
    except InvalidOperation as error:
        raise CopilotCsvError(f"CSV row {line} has an invalid {field}") from error
    if not value.is_finite() or value < 0:
        raise CopilotCsvError(f"CSV row {line} has an invalid {field}")
    return value.quantize(Decimal("0.0001"))


def _source_kind(headers: set[str]) -> CsvSourceKind:
    if {"date", "organization", "username", "model"} <= headers:
        return "ai_usage"
    if {"date", "organization", "username", "product", "sku", "unit_type"} <= headers:
        return "usage_report"
    raise CopilotCsvError(
        "CSV must be a GitHub AI Usage export or Usage Report export"
    )


def parse_copilot_csv(content: bytes) -> ParsedCopilotCsv:
    if not content:
        raise CopilotCsvError("CSV file is empty")
    if len(content) > MAX_CSV_BYTES:
        raise CopilotCsvError("CSV file exceeds the 5 MB limit")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise CopilotCsvError("CSV file must be UTF-8") from error
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames:
        raise CopilotCsvError("CSV file has no header")
    headers = [header.strip() for header in reader.fieldnames]
    if len(headers) > MAX_CSV_COLUMNS or len(set(headers)) != len(headers):
        raise CopilotCsvError("CSV header is invalid")
    if any(not header for header in headers):
        raise CopilotCsvError("CSV header contains an empty column")
    source_kind = _source_kind(set(headers))
    normalized_rows: list[dict[str, Any]] = []
    dates: list[date] = []
    for line, raw in enumerate(reader, start=2):
        if len(normalized_rows) >= MAX_CSV_ROWS:
            raise CopilotCsvError("CSV file exceeds the 25,000 row limit")
        if None in raw:
            raise CopilotCsvError(f"CSV row {line} has too many columns")
        row = {str(key).strip(): (value or "").strip() for key, value in raw.items()}
        usage_date = _date(_required(row, "date", line), line)
        organization = _required(row, "organization", line).lower()
        username = _required(row, "username", line).lower()
        quantity = _decimal(row, "quantity", line, required=True)
        gross = _decimal(row, "gross_amount", line, required=True)
        assert quantity is not None and gross is not None
        discount = _decimal(row, "discount_amount", line) or Decimal("0.0000")
        net = _decimal(row, "net_amount", line)
        if net is None:
            net = max(Decimal("0.0000"), gross - discount)
        model = _required(row, "model", line) if source_kind == "ai_usage" else None
        product = _required(row, "product", line) if source_kind == "usage_report" else None
        sku = _required(row, "sku", line) if source_kind == "usage_report" else None
        unit_type = (
            _required(row, "unit_type", line) if source_kind == "usage_report" else None
        )
        canonical = {
            "source_kind": source_kind,
            "organization": organization,
            "usage_date": usage_date.isoformat(),
            "username": username,
            "model": model,
            "product": product,
            "sku": sku,
            "unit_type": unit_type,
            "cost_center_name": row.get("cost_center_name") or None,
            "quantity": str(quantity),
            "gross_amount": str(gross),
            "discount_amount": str(discount),
            "net_amount": str(net),
            "total_monthly_quota": str(
                _decimal(row, "total_monthly_quota", line) or ""
            ) or None,
        }
        row_sha256 = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        normalized_rows.append(
            {
                **canonical,
                "usage_date": usage_date,
                "quantity": quantity,
                "gross_amount": gross,
                "discount_amount": discount,
                "net_amount": net,
                "total_monthly_quota": (
                    Decimal(canonical["total_monthly_quota"])
                    if canonical["total_monthly_quota"] is not None
                    else None
                ),
                "row_sha256": row_sha256,
                "raw_row": row,
            }
        )
        dates.append(usage_date)
    if not normalized_rows:
        raise CopilotCsvError("CSV file has no data rows")
    return ParsedCopilotCsv(
        source_kind=source_kind,
        content_sha256=hashlib.sha256(content).hexdigest(),
        rows=normalized_rows,
        first_usage_date=min(dates),
        last_usage_date=max(dates),
    )