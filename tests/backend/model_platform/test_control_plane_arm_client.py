from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import httpx
import pytest
from pydantic import HttpUrl, SecretStr

from backend.domain.control_plane import (
    GatewayBackendPoolConfig,
    GatewayBackendPoolMember,
    GatewayPublication,
    GatewayPublicationCreate,
    GatewayRateLimitCircuitBreaker,
    GatewayRateLimitResilience,
    ModelCreateTarget,
    RuntimeTarget,
)
from backend.domain.runtime_models import ProviderTarget
from backend.integrations.apim_control_plane import (
    ApimPolicyCompiler,
    AzureApimPublisherClient,
    BackendCircuitBreakerResource,
    BackendPoolMemberResource,
    BackendPoolResource,
    BackendResource,
    NamedValueResource,
    PolicyCompilationError,
    RetryablePublicationError,
)
from backend.persistence.in_memory import InMemoryRepository
from tests.backend.model_platform.control_plane_support import (
    APIM_ID,
    ROOT,
    FakeApimClient,
    GatewayControlPlaneService,
    GatewayPublicationWorker,
    StubTokenProvider,
    bedrock_publication,
    foundry_publication,
    publisher_settings,
)


def test_arm_client_accepts_redacted_existing_backend_headers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={
                "properties": {
                    "url": "https://observer.example.com",
                    "tls": {
                        "validateCertificateChain": True,
                        "validateCertificateName": True,
                    },
                    "credentials": {
                        "header": {
                            "x-adapter-key": None,
                            "x-turnstile-upstream-host": None,
                        }
                    },
                }
            },
        )

    client = AzureApimPublisherClient(
        publisher_settings(),
        StubTokenProvider(),  # type: ignore[arg-type]
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.ensure_backend(
        BackendResource(
            id="turnstile-obs-existing",
            title="Existing observer",
            url="https://observer.example.com",
            headers=(
                ("x-adapter-key", "{{observer-key}}"),
                ("x-turnstile-upstream-host", "provider.example.com"),
            ),
        )
    )

def test_arm_client_creates_native_pool_and_rate_limit_breakers() -> None:
    writes: dict[str, dict[str, Any]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404)
        writes[request.url.path] = json.loads(request.content)
        return httpx.Response(200, json={})

    settings = publisher_settings()
    client = AzureApimPublisherClient(
        settings,
        StubTokenProvider(),  # type: ignore[arg-type]
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    breaker = BackendCircuitBreakerResource(
        failure_count=1,
        interval_seconds=60,
        trip_duration_seconds=120,
        accept_retry_after=True,
    )
    members = (
        BackendResource(
            id="member-a",
            title="Member A",
            url="https://a.example.com",
            circuit_breaker=breaker,
        ),
        BackendResource(
            id="member-b",
            title="Member B",
            url="https://b.example.com",
            headers=(("api-key", "{{member-b-key}}"),),
            circuit_breaker=breaker,
        ),
    )
    for member in members:
        client.ensure_backend(member)
    client.ensure_backend(
        BackendPoolResource(
            id="pool-a",
            title="Pool A",
            url="",
            members=(
                BackendPoolMemberResource(
                    backend_id="member-a",
                    priority=0,
                    weight=3,
                ),
                BackendPoolMemberResource(
                    backend_id="member-b",
                    priority=1,
                    weight=1,
                ),
            ),
        )
    )

    member_properties = writes[
        f"/subscriptions/{settings.azure_subscription_id}/resourceGroups/"
        f"{settings.apim_resource_group}/providers/Microsoft.ApiManagement/service/"
        f"{settings.apim_service_name}/backends/member-a"
    ]["properties"]
    assert member_properties["type"] == "Single"
    assert member_properties["circuitBreaker"] == {
        "rules": [
            {
                "name": "deployment-fault-isolation",
                "failureCondition": {
                    "count": 1,
                    "interval": "PT60S",
                    "statusCodeRanges": [
                        {"min": 429, "max": 429},
                        {"min": 408, "max": 408},
                        {"min": 500, "max": 599},
                    ],
                    "errorReasons": ["BackendConnectionFailure", "Timeout"],
                },
                "tripDuration": "PT120S",
                "acceptRetryAfter": True,
            }
        ]
    }
    member_b_properties = writes[
        f"/subscriptions/{settings.azure_subscription_id}/resourceGroups/"
        f"{settings.apim_resource_group}/providers/Microsoft.ApiManagement/service/"
        f"{settings.apim_service_name}/backends/member-b"
    ]["properties"]
    assert member_b_properties["credentials"]["header"] == {
        "api-key": ["{{member-b-key}}"]
    }
    pool_properties = writes[
        f"/subscriptions/{settings.azure_subscription_id}/resourceGroups/"
        f"{settings.apim_resource_group}/providers/Microsoft.ApiManagement/service/"
        f"{settings.apim_service_name}/backends/pool-a"
    ]["properties"]
    assert pool_properties["type"] == "Pool"
    assert pool_properties["pool"]["services"] == [
        {
            "id": (
                f"/subscriptions/{settings.azure_subscription_id}/resourceGroups/"
                f"{settings.apim_resource_group}/providers/Microsoft.ApiManagement/"
                f"service/{settings.apim_service_name}/backends/member-a"
            ),
            "priority": 1,
            "weight": 3,
        },
        {
            "id": (
                f"/subscriptions/{settings.azure_subscription_id}/resourceGroups/"
                f"{settings.apim_resource_group}/providers/Microsoft.ApiManagement/"
                f"service/{settings.apim_service_name}/backends/member-b"
            ),
            "priority": 2,
            "weight": 1,
        },
    ]

def test_arm_client_pool_readback_is_immutable_and_idempotent() -> None:
    settings = publisher_settings()
    base = (
        f"/subscriptions/{settings.azure_subscription_id}/resourceGroups/"
        f"{settings.apim_resource_group}/providers/Microsoft.ApiManagement/service/"
        f"{settings.apim_service_name}"
    )
    writes = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal writes
        if request.method == "PUT":
            writes += 1
        return httpx.Response(
            200,
            json={
                "properties": {
                    "type": "Pool",
                    "pool": {
                        "services": [
                            {
                                "id": f"{base}/backends/member-a",
                                "priority": 1,
                                "weight": 1,
                            },
                            {
                                "id": f"{base}/backends/member-b",
                                "priority": 1,
                                "weight": 1,
                            },
                        ]
                    },
                }
            },
        )

    client = AzureApimPublisherClient(
        settings,
        StubTokenProvider(),  # type: ignore[arg-type]
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    pool = BackendPoolResource(
        id="pool-a",
        title="Pool A",
        url="",
        members=(
            BackendPoolMemberResource("member-a", 0, 1),
            BackendPoolMemberResource("member-b", 0, 1),
        ),
    )

    client.ensure_backend(pool)
    assert writes == 0

    changed = pool.__class__(
        id=pool.id,
        title=pool.title,
        url="",
        members=(
            BackendPoolMemberResource("member-a", 0, 2),
            BackendPoolMemberResource("member-b", 0, 1),
        ),
    )
    with pytest.raises(PolicyCompilationError, match="different settings"):
        client.ensure_backend(changed)

def test_foundry_candidate_probes_chat_and_responses_before_promotion() -> None:
    repository = InMemoryRepository()
    foundry_provider_ids = {
        item["id"]
        for item in repository.providers
        if item["brand_key"] == "microsoft_foundry"
    }
    repository.models = [
        item for item in repository.models if item["provider_id"] not in foundry_provider_ids
    ]
    repository.runtimes = [
        item for item in repository.runtimes if item["provider_id"] not in foundry_provider_ids
    ]
    repository.providers = [
        item for item in repository.providers if item["id"] not in foundry_provider_ids
    ]
    publication = GatewayControlPlaneService(
        repository,
        apim_principal_id="39deeba0-9799-4806-96c4-2f65eb0f22d1",
    ).publish(foundry_publication("gpt-5.6-luna"), "owner@example.com")
    alias = publication.desired_spec.bindings[-1].model.model_key
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": alias}]})
        payload = json.loads(request.content)
        assert payload["model"] == alias
        if request.url.path.endswith("/responses/compact"):
            assert payload == {"model": alias, "input": "Reply only with OK."}
            return httpx.Response(
                200,
                json={
                    "object": "response.compaction",
                    "output": [{"type": "compaction", "encrypted_content": "opaque"}],
                    "usage": {
                        "input_tokens": 2,
                        "output_tokens": 1,
                        "total_tokens": 3,
                    },
                },
            )
        if request.url.path.endswith("/responses"):
            assert payload["input"] == "Reply only with OK."
            assert payload["max_output_tokens"] == 16
            assert payload["store"] is False
            assert "messages" not in payload
            return httpx.Response(
                200,
                json={"output": [{"type": "message", "content": []}]},
            )
        assert request.url.path.endswith("/chat/completions")
        assert payload["max_completion_tokens"] == 8
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "OK"}}]},
        )

    settings = publisher_settings().model_copy(
        update={
            "apim_probe_subscription_key": SecretStr("probe-key"),
            "apim_regression_model_key": alias,
        }
    )
    client = AzureApimPublisherClient(
        settings,
        StubTokenProvider(),  # type: ignore[arg-type]
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.probe_revision("turnstile-test", publication)

    assert [
        request.url.path for request in requests if request.method == "POST"
    ] == [
        "/turnstile/llm;rev=turnstile-test/chat/completions",
        "/turnstile/llm;rev=turnstile-test/chat/completions",
        "/turnstile/llm;rev=turnstile-test/responses",
        "/turnstile/llm;rev=turnstile-test/responses/compact",
    ]

def test_pool_candidate_probe_covers_every_member_and_operation() -> None:
    repository = InMemoryRepository()
    provider = next(
        item
        for item in repository.providers
        if item["brand_key"] == "microsoft_foundry"
    )
    runtime = next(
        item for item in repository.runtimes if item["provider_id"] == provider["id"]
    )
    primary_url = "https://probe-primary.services.ai.azure.com/openai/v1"
    runtime["config"].update(
        control_plane_managed=True,
        project_endpoint="https://probe-primary.services.ai.azure.com/api/projects/main",
        backend_url=primary_url,
        backend_path="/openai/v1/chat/completions",
        auth_strategy="managed_identity",
        managed_identity_resource="https://ai.azure.com",
        streaming_mode="native",
    )
    publication = GatewayControlPlaneService(repository).publish(
        GatewayPublicationCreate(
            gateway_profile_id=APIM_ID,
            provider=ProviderTarget(existing_id=provider["id"]),
            runtime=RuntimeTarget(existing_id=runtime["id"]),
            model=ModelCreateTarget(deployment_name="gpt-5.6-probe-pool"),
        ),
        "owner@example.com",
    )
    binding = publication.desired_spec.bindings[-1]
    pool = GatewayBackendPoolConfig(
        members=[
            GatewayBackendPoolMember(
                runtime_id=runtime["id"],
                runtime_name=runtime["name"],
                backend_url=HttpUrl(primary_url),
                priority=0,
                weight=1,
            ),
            GatewayBackendPoolMember(
                runtime_id=UUID("30000000-0000-4000-8000-000000000097"),
                runtime_name="Probe secondary runtime",
                backend_url=HttpUrl(
                    "https://probe-secondary.services.ai.azure.com/openai/v1"
                ),
                priority=0,
                weight=1,
            ),
        ],
        rate_limit=GatewayRateLimitResilience(
            max_attempts_per_request=2,
            retry_interval_seconds=1,
            first_fast_retry=True,
            circuit_breaker=GatewayRateLimitCircuitBreaker(
                failure_count=1,
                interval_seconds=60,
                trip_duration_seconds=60,
                accept_retry_after=True,
            ),
        ),
    )
    pooled_binding = binding.model_copy(
        update={
            "runtime_config": {
                **binding.runtime_config,
                "apim_backend_pool": pool.model_dump(mode="json"),
            }
        }
    )
    publication = publication.model_copy(
        update={
            "desired_spec": publication.desired_spec.model_copy(
                update={"bindings": [pooled_binding]}
            )
        }
    )
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": item.id}
                        for item in publication.desired_spec.discovery_models
                    ]
                },
            )
        member_id = request.headers["x-turnstile-pool-member"]
        calls.append((member_id, request.url.path.rsplit("/", 1)[-1]))
        if request.url.path.endswith("/responses/compact"):
            return httpx.Response(
                200,
                json={
                    "object": "response.compaction",
                    "output": [{"type": "compaction", "encrypted_content": "opaque"}],
                    "usage": {
                        "input_tokens": 2,
                        "output_tokens": 1,
                        "total_tokens": 3,
                    },
                },
            )
        if request.url.path.endswith("/responses"):
            return httpx.Response(200, json={"output": []})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "OK"}}]},
        )

    client = AzureApimPublisherClient(
        publisher_settings().model_copy(
            update={
                "apim_probe_subscription_key": SecretStr("probe-key"),
                "apim_regression_model_key": binding.model.model_key,
            }
        ),
        StubTokenProvider(),  # type: ignore[arg-type]
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.probe_revision("turnstile-pool", publication)

    member_ids = {
        ApimPolicyCompiler._pool_member_backend_id(publication, binding, member)
        for member in pool.members
    }
    assert {member_id for member_id, _ in calls} == member_ids
    assert len(calls) == 6
    assert all(
        sum(1 for observed, _ in calls if observed == member_id) == 3
        for member_id in member_ids
    )

def test_foundry_removal_probe_requires_survivor_and_removed_alias_rejection() -> None:
    repository = InMemoryRepository()
    model = next(
        item for item in repository.models if item["model_key"] == "gpt-5.6-luna"
    )
    publication = GatewayControlPlaneService(repository).remove_model(
        model["id"], "owner@example.com"
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": item.id}
                        for item in publication.desired_spec.discovery_models
                    ]
                },
            )
        payload = json.loads(request.content)
        if request.url.path.endswith("/responses/compact"):
            assert payload == {
                "model": model["model_key"],
                "input": "Reply only with OK.",
            }
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "The requested model is not published by this gateway revision"
                    }
                },
            )
        if request.url.path.endswith("/responses"):
            assert payload["max_output_tokens"] == 16
            assert payload["store"] is False
            assert "messages" not in payload
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "The requested model is not published by this gateway revision"
                    }
                },
            )
        assert payload["max_completion_tokens"] == 8
        assert "max_tokens" not in payload
        if payload["model"] == model["model_key"]:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "The requested model is not published by this gateway revision"
                    }
                },
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    settings = publisher_settings().model_copy(
        update={"apim_probe_subscription_key": SecretStr("probe-key")}
    )
    client = AzureApimPublisherClient(
        settings,
        StubTokenProvider(),  # type: ignore[arg-type]
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.probe_revision("turnstile-test", publication)

    post_paths = [request.url.path for request in requests if request.method == "POST"]
    assert post_paths == [
        "/turnstile/llm;rev=turnstile-test/chat/completions",
        "/turnstile/llm;rev=turnstile-test/chat/completions",
        "/turnstile/llm;rev=turnstile-test/responses",
        "/turnstile/llm;rev=turnstile-test/responses/compact",
    ]
    probe_headers = requests[-1].headers
    assert probe_headers["x-user-id"] == "system-gateway-publication"
    assert probe_headers["x-hive-user"] == "Turnstile Publisher"
    assert probe_headers["x-hive-workflow"] == "gateway-publication-probe"

def test_only_new_model_assignment_denial_is_retryable() -> None:
    responses = iter(
        [
            httpx.Response(403, json={"error": "model_not_assigned"}),
            httpx.Response(403, json={"error": "model_not_assigned"}),
        ]
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return next(responses)

    client = AzureApimPublisherClient(
        publisher_settings(),
        StubTokenProvider(),  # type: ignore[arg-type]
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(httpx.HTTPStatusError):
        client._probe_model("https://candidate", {}, "regression")
    with pytest.raises(RetryablePublicationError):
        client._probe_model(
            "https://candidate",
            {},
            "new-model",
            retry_assignment_denial=True,
        )

def test_live_parent_policy_drift_fails_before_candidate_policy_write() -> None:
    repository = InMemoryRepository()
    GatewayControlPlaneService(repository).publish(bedrock_publication(), "owner@example.com")
    client = FakeApimClient()
    worker = GatewayPublicationWorker(
        repository,
        client,
        (ROOT / "infra/policies/foundry-finops-policy.xml").read_text(),
    )
    for _ in range(3):
        assert worker.run_once("worker-a") is not None
    client.policy += "\n<!-- manual hotfix -->"

    failed = worker.run_once("worker-a")

    assert failed is not None and failed.status == "failed"
    assert "changed after publication was queued" in (failed.error_message or "")
    assert not any(name == "api-policy" for name, _ in client.calls)

def test_activation_failure_after_promotion_converges_without_failed_state() -> None:
    repository = InMemoryRepository()
    GatewayControlPlaneService(repository).publish(bedrock_publication(), "owner@example.com")
    client = FakeApimClient()
    worker = GatewayPublicationWorker(
        repository,
        client,
        (ROOT / "infra/policies/foundry-finops-policy.xml").read_text(),
    )
    for _ in range(5):
        assert worker.run_once("worker-a") is not None

    original_activate = repository.activate_gateway_publication
    failed_once = False

    def flaky_activate(publication_id: UUID, actor: str) -> dict[str, object]:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("database temporarily unavailable")
        return original_activate(publication_id, actor)

    repository.activate_gateway_publication = flaky_activate  # type: ignore[method-assign]
    converging = worker.run_once("worker-a")
    active = worker.run_once("worker-a")

    assert converging is not None and converging.status == "promoting"
    assert active is not None and active.status == "active"

def test_arm_client_clones_revision_from_current_api() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and ";rev=turnstile-1-test" in request.url.path:
            return httpx.Response(404, json={"error": {"code": "NotFound"}})
        if request.method == "GET" and request.url.path.endswith("/apis/turnstile-llm"):
            return httpx.Response(
                200,
                json={
                    "id": "/subscriptions/s/resourceGroups/r/providers/"
                    "Microsoft.ApiManagement/service/a/apis/turnstile-llm",
                    "properties": {
                        "path": "finops/llm",
                        "serviceUrl": "https://legacy.example",
                    },
                },
            )
        return httpx.Response(201, json={"properties": {"provisioningState": "InProgress"}})

    client = AzureApimPublisherClient(
        publisher_settings(),
        StubTokenProvider(),  # type: ignore[arg-type]
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client.ensure_revision("turnstile-1-test", "Candidate")

    create = requests[-1]
    body = json.loads(create.content)
    assert create.method == "PUT"
    assert create.url.path.endswith("/apis/turnstile-llm;rev=turnstile-1-test")
    assert body["properties"] == {
        "path": "finops/llm",
        "sourceApiId": "/subscriptions/s/resourceGroups/r/providers/"
        "Microsoft.ApiManagement/service/a/apis/turnstile-llm",
        "apiRevisionDescription": "Candidate",
        "serviceUrl": "https://legacy.example",
    }

@pytest.mark.parametrize(
    ("content_type", "body", "expected"),
    [
        ("application/vnd.ms-azure-apim.policy+xml", "<policies />", "<policies />"),
        (
            "application/vnd.ms-azure-apim.policy+xml",
            '<policies value="&amp;quot;model&amp;quot;" />',
            '<policies value="&quot;model&quot;" />',
        ),
        (
            "application/json",
            json.dumps({"properties": {"value": "<policies />"}}),
            "<policies />",
        ),
    ],
)
def test_arm_client_reads_current_policy_wire_formats(
    content_type: str, body: str, expected: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/apis/turnstile-llm"):
            return httpx.Response(200, json={"properties": {"apiRevision": "1"}})
        assert request.url.path.endswith(
            "/apis/turnstile-llm;rev=1/policies/policy"
        )
        return httpx.Response(
            200,
            text=body,
            headers={"Content-Type": content_type},
        )

    client = AzureApimPublisherClient(
        publisher_settings(),
        StubTokenProvider(),  # type: ignore[arg-type]
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.current_api_policy() == ("1", expected)

def test_arm_client_creates_tls_backend_and_secret_named_values() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(404, json={"error": {"code": "NotFound"}})
        return httpx.Response(201, json={})

    client = AzureApimPublisherClient(
        publisher_settings(),
        StubTokenProvider(),  # type: ignore[arg-type]
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client.ensure_backend(
        BackendResource(
            id="turnstile-dyn-test-1",
            title="FinOps Bedrock",
            url="https://bedrock-runtime.ap-southeast-2.amazonaws.com",
            headers=(
                ("x-adapter-key", "{{turnstile-envoy-adapter-key}}"),
                (
                    "x-turnstile-upstream-host",
                    "bedrock-runtime.ap-southeast-2.amazonaws.com",
                ),
            ),
        )
    )
    client.ensure_named_value(
        NamedValueResource(
            id="bedrock-bearer-key",
            key_vault_secret_id="https://vault.vault.azure.net/secrets/bedrock-key",
        )
    )
    client.ensure_named_value(
        NamedValueResource(
            id="bedrock-direct-key",
            key_vault_secret_id=None,
            owner_publication_id="publication-one",
            value="one-time-direct-key",
        )
    )
    client.put_operation_policy(
        "turnstile-1-test", "anthropic-messages", "<policies />"
    )

    backend = next(
        request
        for request in requests
        if "/backends/" in request.url.path and request.method == "PUT"
    )
    backend_body = json.loads(backend.content)["properties"]
    assert backend_body["tls"] == {
        "validateCertificateChain": True,
        "validateCertificateName": True,
    }
    assert backend_body["credentials"]["header"] == {
        "x-adapter-key": ["{{turnstile-envoy-adapter-key}}"],
        "x-turnstile-upstream-host": [
            "bedrock-runtime.ap-southeast-2.amazonaws.com"
        ],
    }
    key_vault_value = next(
        request
        for request in requests
        if request.url.path.endswith("/namedValues/bedrock-bearer-key")
        and request.method == "PUT"
    )
    assert json.loads(key_vault_value.content)["properties"]["keyVault"][
        "secretIdentifier"
    ] == "https://vault.vault.azure.net/secrets/bedrock-key"
    direct_value = next(
        request
        for request in requests
        if request.url.path.endswith("/namedValues/bedrock-direct-key")
        and request.method == "PUT"
    )
    direct_properties = json.loads(direct_value.content)["properties"]
    assert direct_properties["secret"] is True
    assert direct_properties["value"] == "one-time-direct-key"
    assert direct_properties["tags"] == ["turnstile_publication_publication_one"]
    assert all(
        character.isalnum() or character in "._"
        for character in direct_properties["tags"][0]
    )
    assert "one-time-direct-key" not in repr(
        NamedValueResource(
            id="bedrock-direct-key",
            key_vault_secret_id=None,
            owner_publication_id="publication-one",
            value="one-time-direct-key",
        )
    )
    policy = requests[-1]
    assert policy.headers["If-Match"] == "*"
    assert "bedrock-key" not in policy.content.decode("utf-8")

def test_arm_client_refreshes_an_existing_direct_secret_named_value() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "properties": {
                        "displayName": "managed-key",
                        "secret": True,
                        "tags": ["turnstile_publication_publication_one"],
                    }
                },
            )
        return httpx.Response(200, json={})

    client = AzureApimPublisherClient(
        publisher_settings(),
        StubTokenProvider(),  # type: ignore[arg-type]
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.ensure_named_value(
        NamedValueResource(
            id="managed-key",
            key_vault_secret_id=None,
            owner_publication_id="publication-one",
            value="replacement-key",
        )
    )

    update = next(request for request in requests if request.method == "PUT")
    properties = json.loads(update.content)["properties"]
    assert properties == {
        "displayName": "managed-key",
        "secret": True,
        "value": "replacement-key",
        "tags": ["turnstile_publication_publication_one"],
    }

def test_arm_client_never_refreshes_another_publications_direct_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "properties": {
                        "displayName": "managed-key",
                        "secret": True,
                        "tags": ["turnstile_publication_publication_one"],
                    }
                },
            )
        raise AssertionError("A foreign publication must not overwrite this secret")

    client = AzureApimPublisherClient(
        publisher_settings(),
        StubTokenProvider(),  # type: ignore[arg-type]
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(PolicyCompilationError, match="another publication"):
        client.ensure_named_value(
            NamedValueResource(
                id="managed-key",
                key_vault_secret_id=None,
                owner_publication_id="publication-two",
                value="replacement-key",
            )
        )


def _active_bedrock_release() -> tuple[GatewayPublication, FakeApimClient]:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(repository)
    fake = FakeApimClient()
    worker = GatewayPublicationWorker(repository, fake, fake.policy)
    service.publish(bedrock_publication("dependency-check"), "owner@example.com")
    release = None
    for _ in range(8):
        release = worker.run_once("worker")
        assert release is not None
        if release.status == "active":
            break
    assert release is not None and release.status == "active"
    return release, fake


@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (None, "healthy"),
        ("missing_backend", "missing"),
        ("mismatched_named_value", "mismatched"),
    ],
)
def test_arm_client_inspects_recorded_release_dependencies(
    failure: str | None,
    expected_status: str,
) -> None:
    release, fake = _active_bedrock_release()
    assert release.apim_revision is not None
    revision = release.apim_revision
    raw_backends = release.resource_manifest.get("backends")
    assert isinstance(raw_backends, list)
    backend_ids = [str(value) for value in raw_backends]

    def backend_properties(backend_id: str) -> dict[str, Any]:
        backend = fake.backends[backend_id]
        if isinstance(backend, BackendPoolResource):
            return {
                "type": "Pool",
                "pool": {
                    "services": [
                        {
                            "id": (
                                f"/subscriptions/00000000-0000-0000-0000-000000000001"
                                f"/resourceGroups/rg-finops/providers/Microsoft.ApiManagement"
                                f"/service/apim-finops/backends/{member.backend_id}"
                            ),
                            "priority": member.priority + 1,
                            "weight": member.weight,
                        }
                        for member in backend.members
                    ]
                },
            }
        properties: dict[str, Any] = {
            "type": "Single",
            "url": backend.url,
            "tls": {
                "validateCertificateChain": True,
                "validateCertificateName": True,
            },
        }
        if backend.headers:
            properties["credentials"] = {
                "header": {name: [value] for name, value in backend.headers}
            }
        return properties

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/backends/" in path:
            backend_id = path.rsplit("/", 1)[-1]
            if failure == "missing_backend" and backend_id == backend_ids[0]:
                return httpx.Response(404)
            return httpx.Response(
                200, json={"properties": backend_properties(backend_id)}
            )
        if "/namedValues/" in path:
            named_value_id = path.rsplit("/", 1)[-1]
            expected = next(
                value
                for value in reversed(fake.named_values)
                if value.id == named_value_id
            )
            properties: dict[str, Any] = {
                "secret": failure != "mismatched_named_value"
            }
            if expected.key_vault_secret_id is not None:
                properties["keyVault"] = {
                    "secretIdentifier": expected.key_vault_secret_id
                }
            return httpx.Response(200, json={"properties": properties})
        if path.endswith("/policies/policy"):
            operation = next(
                (
                    key
                    for key in fake.operation_policies
                    if f"/operations/{key}/" in path
                ),
                None,
            )
            value = (
                fake.operation_policies[operation]
                if operation
                else fake.api_policies[revision]
            )
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"properties": {"value": value}},
            )
        return httpx.Response(200, json={"properties": {"provisioningState": "Succeeded"}})

    client = AzureApimPublisherClient(
        publisher_settings(),
        StubTokenProvider(),  # type: ignore[arg-type]
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = client.inspect_revision_dependencies(release)

    assert result.live_status == expected_status
    assert (f"missing_backend:{backend_ids[0]}" in result.issues) is (
        failure == "missing_backend"
    )
    assert any(
        issue.startswith("mismatched_named_value_identity:")
        for issue in result.issues
    ) is (failure == "mismatched_named_value")


def test_gc_plan_protects_policy_and_pool_references() -> None:
    release, _ = _active_bedrock_release()
    assert release.apim_revision is not None
    current = release.model_copy(
        update={
            "id": UUID("f0000000-0000-4000-8000-000000000002"),
            "generation": release.generation + 1,
            "apim_revision": "current-revision",
            "resource_manifest": {
                **release.resource_manifest,
                "backends": [],
                "named_values": [],
            },
        }
    )
    expired = release.model_copy(
        update={
            "apim_revision": "expired-revision",
            "resource_manifest": {
                **release.resource_manifest,
                "backends": [
                    "pool-protected",
                    "member-protected",
                    "fragment-protected",
                    "backend-protected",
                    "backend-orphan",
                ],
                "named_values": ["named-value-protected", "backend-named-value"],
            },
        }
    )
    product_linked = release.model_copy(
        update={
            "id": UUID("f0000000-0000-4000-8000-000000000003"),
            "apim_revision": "product-protected-revision",
            "resource_manifest": {
                **release.resource_manifest,
                "backends": [],
                "named_values": [],
            },
        }
    )
    settings = publisher_settings()
    resource_base = (
        f"/subscriptions/{settings.azure_subscription_id}"
        f"/resourceGroups/{settings.apim_resource_group}"
        f"/providers/Microsoft.ApiManagement/service/{settings.apim_service_name}"
    )

    def resource(resource_type: str, name: str, properties: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": f"{resource_base}/{resource_type}/{name}",
            "properties": properties,
        }

    def listed(values: list[dict[str, Any]]) -> httpx.Response:
        return httpx.Response(200, json={"value": values})

    def policy(value: str) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"properties": {"value": value}},
        )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        path = request.url.path
        query = request.url.query.decode()
        if (
            path.endswith("/apis")
            and "expandApiVersionSet=true" in query
            and "includeRevisions=true" in query
        ):
            return listed(
                [
                    resource(
                        "apis",
                        "turnstile-llm;rev=expired-revision",
                        {"apiRevision": "expired-revision"},
                    ),
                    resource(
                        "apis",
                        "turnstile-llm;rev=current-revision",
                        {"apiRevision": "current-revision"},
                    ),
                    resource(
                        "apis",
                        "turnstile-llm;rev=product-protected-revision",
                        {"apiRevision": "product-protected-revision"},
                    ),
                ]
            )
        if path.endswith("/apis/turnstile-llm"):
            return httpx.Response(200, json={"properties": {"apiRevision": "current-revision"}})
        if path.endswith("/apis/turnstile-llm/releases"):
            return listed(
                [
                    resource(
                        "apis/turnstile-llm/releases",
                        "product-rollback-asset",
                        {},
                    ),
                    resource(
                        "apis/turnstile-llm/releases",
                        "current-release-asset",
                        {},
                    ),
                ]
            )
        if path.endswith("/releases/product-rollback-asset"):
            return httpx.Response(
                200,
                json={
                    "properties": {
                        "apiId": (
                            f"{resource_base}/apis/turnstile-llm;rev="
                            "product-protected-revision"
                        )
                    }
                },
            )
        if path.endswith("/releases/current-release-asset"):
            return httpx.Response(
                200,
                json={
                    "properties": {
                        "apiId": f"{resource_base}/apis/turnstile-llm"
                    }
                },
            )
        if path.endswith(";rev=current-revision/policies/policy"):
            return policy(
                '<set-backend-service backend-id="pool-protected" />'
                '<set-backend-service backend-id="backend-protected" />'
            )
        if path.endswith(";rev=product-protected-revision/policies/policy"):
            return policy("<policies />")
        if path.endswith(";rev=current-revision/operations") or path.endswith(
            ";rev=product-protected-revision/operations"
        ):
            return listed([])
        if path.endswith("/policies/policy") and "/products/" not in path:
            return httpx.Response(404)
        if path.endswith("/products"):
            return listed([resource("products", "product-one", {})])
        if path.endswith("/products/product-one/policies/policy"):
            return policy("{{named-value-protected}}")
        if path.endswith("/products/product-one/apis"):
            return listed(
                [
                    resource(
                        "products/product-one/apis",
                        "turnstile-llm",
                        {"apiRevision": "product-protected-revision"},
                    )
                ]
            )
        if path.endswith("/policyFragments"):
            return listed([resource("policyFragments", "fragment-one", {})])
        if path.endswith("/policyFragments/fragment-one"):
            return httpx.Response(
                200,
                json={"properties": {"value": "fragment-protected"}},
            )
        if path.endswith("/backends"):
            return listed(
                [
                    resource(
                        "backends",
                        "pool-protected",
                        {
                            "type": "Pool",
                            "pool": {
                                "services": [
                                    {"id": f"{resource_base}/backends/member-protected"}
                                ]
                            },
                        },
                    ),
                    resource("backends", "member-protected", {"type": "Single"}),
                    resource("backends", "fragment-protected", {"type": "Single"}),
                    resource(
                        "backends",
                        "backend-protected",
                        {
                            "type": "Single",
                            "credentials": {
                                "header": {"api-key": ["{{backend-named-value}}"]}
                            },
                        },
                    ),
                    resource("backends", "backend-orphan", {"type": "Single"}),
                ]
            )
        if path.endswith("/namedValues"):
            return listed(
                [
                    resource("namedValues", "named-value-protected", {}),
                    resource("namedValues", "backend-named-value", {}),
                ]
            )
        raise AssertionError(f"Unexpected APIM graph request: {request.url}")

    client = AzureApimPublisherClient(
        settings,
        StubTokenProvider(),  # type: ignore[arg-type]
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    evidence = client.plan_release_garbage_collection(
        [expired, current, product_linked], {str(current.id)}
    )
    repeated = client.plan_release_garbage_collection(
        [expired, current, product_linked], {str(current.id)}
    )
    candidates = {
        (value["resource_type"], value["resource_id"])
        for value in evidence.candidates
    }

    assert repeated.candidates == evidence.candidates
    assert repeated.reference_graph_sha256 == evidence.reference_graph_sha256
    assert ("api_revision", "expired-revision") in candidates
    assert ("backend", "backend-orphan") in candidates
    assert ("backend_pool", "pool-protected") not in candidates
    assert ("backend", "member-protected") not in candidates
    assert ("backend", "fragment-protected") not in candidates
    assert ("backend", "backend-protected") not in candidates
    assert ("named_value", "named-value-protected") not in candidates
    assert ("named_value", "backend-named-value") not in candidates
    assert ("api_revision", "product-protected-revision") not in candidates


def test_gc_plan_rejects_pagination_outside_the_configured_apim_service() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/apis")
        return httpx.Response(
            200,
            json={
                "value": [],
                "nextLink": (
                    "https://management.azure.com/subscriptions/other/resourceGroups/other/"
                    "providers/Microsoft.ApiManagement/service/other/apis"
                    "?api-version=2024-05-01"
                ),
            },
        )

    client = AzureApimPublisherClient(
        publisher_settings(),
        StubTokenProvider(),  # type: ignore[arg-type]
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(PolicyCompilationError, match="untrusted pagination URL"):
        client.plan_release_garbage_collection([], set())


def test_apim_list_accepts_same_service_pagination_on_explicit_https_port() -> None:
    settings = publisher_settings()
    resource_base = (
        f"/subscriptions/{settings.azure_subscription_id}"
        f"/resourceGroups/{settings.apim_resource_group}"
        f"/providers/Microsoft.ApiManagement/service/{settings.apim_service_name}"
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        assert request.method == "GET"
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "value": [{"id": f"{resource_base}/backends/first"}],
                    "nextLink": (
                        f"https://management.azure.com:443{resource_base}/backends"
                        "?api-version=2024-05-01&$skip=next"
                    ),
                },
            )
        return httpx.Response(
            200,
            json={"value": [{"id": f"{resource_base}/backends/second"}]},
        )

    client = AzureApimPublisherClient(
        settings,
        StubTokenProvider(),  # type: ignore[arg-type]
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert [value["id"].rsplit("/", 1)[-1] for value in client._list_all("/backends")] == [
        "first",
        "second",
    ]
    assert calls == 2
