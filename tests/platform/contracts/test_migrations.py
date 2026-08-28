from __future__ import annotations

import re

from tests.support.paths import REPOSITORY_ROOT

MIGRATIONS = REPOSITORY_ROOT / "migrations"
INITIAL_SCHEMA = MIGRATIONS / "001_initial_schema.up.sql"


def schema() -> str:
    return INITIAL_SCHEMA.read_text(encoding="utf-8")


def table_names(sql: str) -> set[str]:
    return set(
        re.findall(
            r"^CREATE TABLE (?:public\.)?([a-z_]+)\s*\(",
            sql,
            flags=re.MULTILINE,
        )
    )


def test_repository_starts_with_one_clean_install_migration() -> None:
    assert [path.name for path in sorted(MIGRATIONS.glob("*.up.sql"))] == [
        "001_initial_schema.up.sql"
    ]
    assert not list(MIGRATIONS.glob("*.down.sql"))


def test_initial_schema_excludes_router_and_historical_ledger_objects() -> None:
    sql = schema().lower()

    assert "model_router" not in sql
    assert "router_id" not in sql
    assert "router_name" not in sql
    assert "create table public.schema_migration" not in sql
    assert "alter table only public.schema_migration" not in sql
    assert "copy public.schema_migration" not in sql


def test_initial_schema_contains_no_customer_or_model_seed_data() -> None:
    sql = schema()

    assert "COPY public." not in sql
    for table in (
        "app_user",
        "managed_model",
        "model_provider",
        "model_runtime",
        "token_usage",
        "user_model_access",
    ):
        assert f"INSERT INTO public.{table}" not in sql
        assert f"INSERT INTO {table}" not in sql


def test_initial_schema_seeds_only_the_platform_apim_gateway() -> None:
    sql = schema()

    assert sql.count("INSERT INTO public.gateway_profile") == 1
    assert "Azure API Management" in sql
    assert "'apim'" in sql
    assert '"header_name": "Ocp-Apim-Subscription-Key"' in sql
    assert "LiteLLM" not in sql


def test_initial_schema_contains_current_platform_tables() -> None:
    tables = table_names(schema())
    expected = {
        "anomaly_rule",
        "app_user",
        "assistant_conversation",
        "assistant_setting",
        "assistant_turn",
        "apim_cache_read_hourly",
        "copilot_connection",
        "copilot_oauth_setting",
        "copilot_oauth_state",
        "gateway_application",
        "gateway_application_audit",
        "gateway_application_avatar",
        "gateway_application_budget",
        "gateway_application_model_access",
        "gateway_application_model_policy",
        "gateway_application_subscription",
        "gateway_profile",
        "gateway_publication",
        "gateway_release_operation",
        "gateway_release_operation_audit",
        "gateway_release_operation_secret",
        "gateway_release_protection",
        "managed_model",
        "model_provider",
        "model_runtime",
        "token_budget",
        "token_usage",
        "token_usage_application_attribution",
        "user_model_access",
        "user_session",
    }

    assert expected <= tables


def test_initial_schema_preserves_key_integrity_constraints() -> None:
    sql = schema()

    assert "model_runtime_gateway_foundry_project_unique_idx" in sql
    assert "gateway_release_operation_one_open_idx" in sql
    assert "UNIQUE (gateway_profile_id, apim_subscription_id)" in sql
    assert "PRIMARY KEY (period_start, application_id)" in sql
    assert "actor_type IN ('person', 'service', 'system')" in sql
    assert "usage_domain IN ('apim', 'github_copilot')" in sql
    assert "octet_length(image_bytes) BETWEEN 1 AND 65536" in sql
    assert "credential_ciphertext BYTEA NOT NULL" in sql
    assert "primary_key" not in sql
    assert "secondary_key" not in sql
