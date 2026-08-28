from __future__ import annotations

import pytest

from backend.data_sources.github_copilot.csv_import import CopilotCsvError, parse_copilot_csv


def test_ai_usage_csv_is_normalized_and_hashed() -> None:
    parsed = parse_copilot_csv(
        b"date,organization,username,model,quantity,gross_amount,discount_amount,net_amount,total_monthly_quota,cost_center_name\n"
        b"2026-08-17,Example-Enterprise,Alice,gpt-5.4,2,1.25,0.25,1.00,100,Platform\n"
    )

    assert parsed.source_kind == "ai_usage"
    assert parsed.first_usage_date.isoformat() == "2026-08-17"
    assert parsed.rows[0]["organization"] == "example-enterprise"
    assert parsed.rows[0]["username"] == "alice"
    assert parsed.rows[0]["model"] == "gpt-5.4"
    assert str(parsed.rows[0]["quantity"]) == "2.0000"
    assert len(parsed.rows[0]["row_sha256"]) == 64
    assert parsed.rows[0]["raw_row"]["organization"] == "Example-Enterprise"


def test_usage_report_csv_is_detected_from_product_sku_and_unit() -> None:
    parsed = parse_copilot_csv(
        b"date,organization,username,product,sku,unit_type,quantity,gross_amount,discount_amount,net_amount\n"
        b"2026-08-17,example-enterprise,bob,Copilot,ai_credits,requests,4,2.5,0.5,2\n"
    )

    assert parsed.source_kind == "usage_report"
    assert parsed.rows[0]["product"] == "Copilot"
    assert parsed.rows[0]["sku"] == "ai_credits"
    assert parsed.rows[0]["unit_type"] == "requests"
    assert parsed.rows[0]["model"] is None


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"date,organization,username\n2026-08-17,x,a\n", "GitHub AI Usage"),
        (
            b"date,organization,username,model,quantity,gross_amount\nnot-a-date,x,a,m,1,1\n",
            "invalid date",
        ),
        (
            b"date,organization,username,model,quantity,gross_amount\n2026-08-17,x,a,m,-1,1\n",
            "invalid quantity",
        ),
    ],
)
def test_invalid_csv_is_rejected(content: bytes, message: str) -> None:
    with pytest.raises(CopilotCsvError, match=message):
        parse_copilot_csv(content)