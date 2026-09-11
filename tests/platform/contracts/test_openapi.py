"""Keeps `contracts/openapi.yaml` honest about the routes the app actually serves.

Contract drift fails nothing: the app keeps working, the linter still passes, and the
document quietly starts describing a different API. The assistant surface reached nine
operations before anyone noticed it was absent from the contract entirely, which is what
this test exists to prevent happening again.

Only paths and methods are compared. Asserting every schema field against the generated
document would fail on formatting differences that mean nothing -- FastAPI emits `anyOf`
where the hand-written contract says `type: [string, 'null']` -- and a test that has to be
loosened after each edit stops being read.

PyYAML is a pinned dev dependency rather than an optional import: a guard that skips when
something is missing reports success while checking nothing.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import cast

import yaml  # type: ignore[import-untyped]

from backend.api import app
from tests.support.paths import REPOSITORY_ROOT

CONTRACT = REPOSITORY_ROOT / "contracts" / "openapi.yaml"
METHODS = {"get", "post", "put", "patch", "delete"}

# Documented deliberately as internal, or served by something other than this app.
UNDOCUMENTED = {
    "/health",
    "/{path:path}",
}


def _operations(paths: dict[str, dict[str, object]]) -> set[tuple[str, str]]:
    return {(path, method) for path, item in paths.items() for method in item if method in METHODS}


@cache
def _yaml_document(path: Path) -> dict[str, object]:
    return cast(
        dict[str, object],
        yaml.safe_load(path.read_text(encoding="utf-8")),
    )


def _resolve_local_ref(ref: str) -> object:
    file_name, fragment = ref.split("#", 1)
    value: object = _yaml_document((CONTRACT.parent / file_name).resolve())
    for raw_part in fragment.removeprefix("/").split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        value = cast(dict[str, object], value)[part]
    return value


def _resolve_index(values: dict[str, object]) -> dict[str, dict[str, object]]:
    def canonicalize(value: object) -> object:
        if isinstance(value, str):
            return value.removeprefix("../../openapi.yaml")
        if isinstance(value, list):
            return [canonicalize(item) for item in value]
        if isinstance(value, dict):
            return {key: canonicalize(item) for key, item in value.items()}
        return value

    resolved: dict[str, dict[str, object]] = {}
    for name, value in values.items():
        item = cast(dict[str, object], value)
        if set(item) == {"$ref"}:
            item = cast(dict[str, object], _resolve_local_ref(cast(str, item["$ref"])))
        resolved[name] = cast(dict[str, object], canonicalize(item))
    return resolved


def _documented_paths() -> dict[str, dict[str, object]]:
    document = _yaml_document(CONTRACT.resolve())
    return _resolve_index(cast(dict[str, object], document["paths"]))


def _documented_schemas() -> dict[str, dict[str, object]]:
    document = _yaml_document(CONTRACT.resolve())
    components = cast(dict[str, object], document["components"])
    return _resolve_index(cast(dict[str, object], components["schemas"]))


def test_openapi_root_is_a_small_domain_index() -> None:
    root = _yaml_document(CONTRACT.resolve())
    paths = cast(dict[str, dict[str, str]], root["paths"])
    components = cast(dict[str, object], root["components"])
    schemas = cast(dict[str, dict[str, str]], components["schemas"])

    assert len(CONTRACT.read_text(encoding="utf-8").splitlines()) < 800
    assert len(paths) == 76
    assert len(schemas) == 161
    assert all(set(value) == {"$ref"} for value in paths.values())
    assert all(set(value) == {"$ref"} for value in schemas.values())
    assert {path.name for path in (CONTRACT.parent / "openapi" / "paths").glob("*.yaml")} == {
        "application-access.yaml",
        "assistant.yaml",
        "authentication.yaml",
        "budgets.yaml",
        "github-copilot.yaml",
        "model-platform.yaml",
        "observability.yaml",
    }


def test_application_ledger_fields_preserve_unknown_and_zero() -> None:
    schema = _documented_schemas()["GatewayApplicationBudget"]
    properties = cast(dict[str, dict[str, object]], schema["properties"])
    numeric = (
        "pending_reserved_tokens",
        "pending_reservation_count",
        "finalized_upper_bound_tokens",
        "finalized_upper_bound_count",
        "stale_reservation_count",
        "available_tokens",
    )
    for field in numeric:
        assert properties[field]["type"] == ["integer", "null"]
        assert properties[field]["default"] is None
        assert properties[field]["minimum"] == 0
    required = cast(list[str], schema["required"])
    assert not set(numeric).intersection(required)
    for field in ("oldest_reservation_at", "ledger_snapshot_at"):
        assert properties[field]["type"] == ["string", "null"]
        assert properties[field]["format"] == "date-time"


def test_event_contract_domain_mirror_matches_the_inline_extension() -> None:
    root = _yaml_document(CONTRACT.resolve())
    mirror = _yaml_document((CONTRACT.parent / "openapi" / "events" / "eventhub.yaml").resolve())[
        "eventContracts"
    ]

    def normalize(value: object) -> object:
        if isinstance(value, str):
            return value.removeprefix("../../openapi.yaml")
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        return value

    assert normalize(mirror) == root["x-event-contracts"]


def _assistant_operations(
    operations: set[tuple[str, str]],
) -> set[tuple[str, str]]:
    return {operation for operation in operations if "/assistant/" in operation[0]}


def test_every_assistant_route_is_in_the_contract() -> None:
    documented = _operations(_documented_paths())
    served = _operations(app.openapi()["paths"])

    missing = sorted(_assistant_operations(served) - _assistant_operations(documented))
    stale = sorted(_assistant_operations(documented) - _assistant_operations(served))

    assert not missing, f"Routes the app serves but the contract omits: {missing}"
    assert not stale, f"Routes the contract claims but the app does not serve: {stale}"


def test_the_contract_never_claims_a_route_that_does_not_exist() -> None:
    """The direction that produces a broken client rather than an undocumented one."""
    documented = _operations(_documented_paths())
    served = _operations(app.openapi()["paths"])
    stale = sorted(op for op in documented - served if op[0] not in UNDOCUMENTED)
    assert not stale, f"Contract describes routes the app does not serve: {stale}"


def test_foundry_key_runtime_contract_is_distinct_from_managed_identity() -> None:
    schema = _documented_schemas()["GatewayRuntimeTarget"]
    alternatives = cast(list[dict[str, object]], schema["oneOf"])

    assert [set(cast(list[str], item["required"])) for item in alternatives] == [
        {"existing_id"},
        {"bedrock_runtime_url", "api_key"},
        {"foundry_project_endpoint"},
        {"foundry_project_endpoint", "foundry_inference_endpoint", "api_key"},
    ]
    managed_identity_exclusions = cast(
        dict[str, list[dict[str, list[str]]]], alternatives[2]["not"]
    )["anyOf"]
    assert {"foundry_inference_endpoint"} in {
        frozenset(item["required"]) for item in managed_identity_exclusions
    }
    assert {"api_key"} in {frozenset(item["required"]) for item in managed_identity_exclusions}


def test_gateway_release_reads_separate_recorded_and_live_integrity() -> None:
    paths = _documented_paths()
    schemas = _documented_schemas()

    def get_operation(path: str) -> dict[str, object]:
        return cast(dict[str, object], paths[path]["get"])

    assert get_operation("/api/v1/model-management/releases")["operationId"] == (
        "listGatewayReleases"
    )
    assert (
        get_operation("/api/v1/model-management/releases/{release_id}")["operationId"]
        == "getGatewayRelease"
    )
    assert (
        get_operation("/api/v1/model-management/releases/{release_id}/diff")["operationId"]
        == "getGatewayReleaseDiff"
    )
    assert (
        get_operation("/api/v1/model-management/releases/{release_id}/integrity")["operationId"]
        == "getGatewayReleaseIntegrity"
    )
    dependencies = cast(
        dict[str, dict[str, object]],
        schemas["GatewayReleaseDependencies"]["properties"],
    )
    assert dependencies["live_status"]["enum"] == [
        "not_checked",
        "healthy",
        "missing",
        "mismatched",
    ]
    summary = cast(
        dict[str, dict[str, object]],
        schemas["GatewayReleaseSummary"]["properties"],
    )
    assert "rollback_eligible" in summary
    assert "rollback_blockers" in summary


def test_native_apim_pool_contract_is_bounded_and_model_scoped() -> None:
    paths = _documented_paths()
    schemas = _documented_schemas()

    pool_path = paths["/api/v1/model-management/models/{item_id}/backend-pool"]
    assert set(pool_path) == {"get", "put", "delete"}
    write = cast(
        dict[str, dict[str, object]],
        schemas["GatewayBackendPoolWrite"]["properties"],
    )
    rate_limit = cast(
        dict[str, dict[str, object]],
        schemas["GatewayRateLimitResilience"]["properties"],
    )
    breaker = cast(
        dict[str, dict[str, object]],
        schemas["GatewayRateLimitCircuitBreaker"]["properties"],
    )

    assert write["members"]["minItems"] == 2
    assert write["members"]["maxItems"] == 30
    assert rate_limit["max_attempts_per_request"] == {
        "type": "integer",
        "const": 2,
    }
    assert breaker["failure_count"] == {"type": "integer", "const": 1}
    assert breaker["interval_seconds"] == {"type": "integer", "const": 60}
    assert breaker["trip_duration_seconds"] == {"type": "integer", "const": 60}
    assert breaker["accept_retry_after"] == {"type": "boolean", "const": True}
    assert rate_limit["backend_timeout_seconds"] == {
        "type": "integer",
        "const": 120,
        "default": 120,
    }
    assert breaker["status_code_ranges"]["default"] == [
        {"minimum": 429, "maximum": 429},
        {"minimum": 408, "maximum": 408},
        {"minimum": 500, "maximum": 599},
    ]
    assert breaker["error_reasons"]["default"] == [
        "BackendConnectionFailure",
        "Timeout",
    ]
    assert "existing compatible managed Runtimes" in str(
        cast(dict[str, object], pool_path["put"])["description"]
    )
