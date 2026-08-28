from __future__ import annotations

from uuid import UUID
from xml.etree import ElementTree

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from pydantic import HttpUrl, SecretStr

from backend.config import Settings
from backend.domain.control_plane import (
    ApiFormat,
    AuthStrategy,
    GatewayBackendFailureStatusCodeRange,
    GatewayBackendPoolConfig,
    GatewayBackendPoolMember,
    GatewayBackendPoolMemberWrite,
    GatewayBackendPoolWrite,
    GatewayCredentialRotation,
    GatewayPublicationCreate,
    GatewayPublicationRetry,
    GatewayRateLimitCircuitBreaker,
    GatewayRateLimitResilience,
    ModelCreateTarget,
    RuntimeTarget,
)
from backend.domain.runtime_models import ProviderTarget
from backend.http.publication_auth import require_publication_owner
from backend.integrations.apim_control_plane import (
    ApimPolicyCompiler,
    BackendPoolResource,
)
from backend.persistence.in_memory import InMemoryRepository
from backend.security import CredentialCipher
from backend.services.control_plane import (
    ControlPlaneConflictError,
)
from tests.backend.model_platform.control_plane_support import (
    APIM_ID,
    FakeApimClient,
    GatewayControlPlaneService,
    GatewayPublicationWorker,
    PublicationAuthStore,
    bedrock_publication,
    external_tenant_foundry_publication,
    foundry_publication,
    publication_request,
)


def test_enabled_control_plane_requires_usage_observer() -> None:
    with pytest.raises(ValueError, match="usage observer"):
        Settings(control_plane_enabled=True)

    settings = Settings(
        control_plane_enabled=True,
        apim_usage_observer_url="https://observer.example.com",
        apim_usage_observer_key_named_value="turnstile-envoy-adapter-key",
    )

    assert settings.apim_usage_observer_url == "https://observer.example.com"

def test_publication_authorization_requires_owner_session_and_allowed_origin() -> None:
    settings = Settings(database_url="postgresql://unused")

    assert require_publication_owner(
        publication_request(), PublicationAuthStore("owner"), settings  # type: ignore[arg-type]
    ) == "owner@example.com"
    with pytest.raises(HTTPException) as member_error:
        require_publication_owner(
            publication_request(),
            PublicationAuthStore("member"),  # type: ignore[arg-type]
            settings,
        )
    assert member_error.value.status_code == 403
    with pytest.raises(HTTPException) as origin_error:
        require_publication_owner(
            publication_request("https://attacker.example"),
            PublicationAuthStore("owner"),  # type: ignore[arg-type]
            settings,
        )
    assert origin_error.value.status_code == 403

def test_foundry_api_key_contract_rejects_incomplete_credentials() -> None:
    with pytest.raises(ValueError, match="inference endpoint and API key"):
        RuntimeTarget(
            foundry_project_endpoint=HttpUrl(
                "https://admin-8382-resource.services.ai.azure.com/api/projects/admin-8382"
            ),
            foundry_inference_endpoint=HttpUrl(
                "https://admin-8382-resource.openai.azure.com/openai/v1"
            ),
        )

def test_foundry_managed_identity_contract_needs_only_project_endpoint() -> None:
    target = RuntimeTarget(
        foundry_project_endpoint=HttpUrl(
            "https://contoso-ai.services.ai.azure.com/api/projects/finops"
        ),
    )

    assert target.foundry_inference_endpoint is None
    assert target.api_key is None

def test_foundry_api_key_contract_rejects_mismatched_resource_endpoint() -> None:
    repository = InMemoryRepository()
    request = external_tenant_foundry_publication(repository)
    request.runtime.foundry_inference_endpoint = HttpUrl(
        "https://attacker-resource.openai.azure.com/openai/v1"
    )

    with pytest.raises(ControlPlaneConflictError, match="same account"):
        GatewayControlPlaneService(repository).publish(request, "owner@example.com")

def test_foundry_project_connection_derives_a_managed_identity_binding() -> None:
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
    ).publish(foundry_publication(), "owner@example.com")
    binding = publication.desired_spec.bindings[-1]

    assert str(binding.backend_url).rstrip("/") == (
        "https://contoso-ai.services.ai.azure.com/api/projects/finops"
    )
    assert binding.backend_path == "/openai/v1/chat/completions"
    assert binding.auth_strategy.value == "managed_identity"
    assert binding.managed_identity_resource == "https://ai.azure.com"
    assert binding.model.upstream_model_id == "gpt-5-mini-deployment"
    assert binding.model.display_name == "gpt-5-mini-deployment · Microsoft Foundry"
    assert binding.model.model_key.startswith("gpt-5-mini-deployment-foundry-")
    assert binding.model.capabilities == ["chat", "tools", "streaming"]
    authorization = binding.runtime_config["authorization"]
    assert isinstance(authorization, dict)
    assert authorization["principal_id"] == (
        "39deeba0-9799-4806-96c4-2f65eb0f22d1"
    )
    assert authorization["role_id"] == (
        "a97b65f3-24c7-4388-baec-2e87135dc908"
    )
    assert authorization["resource_endpoint"] == (
        "https://contoso-ai.services.ai.azure.com"
    )

def test_existing_foundry_claude_uses_anthropic_messages_binding() -> None:
    repository = InMemoryRepository()
    provider = next(
        item
        for item in repository.providers
        if item["brand_key"] == "microsoft_foundry"
    )
    runtime = next(
        item for item in repository.runtimes if item["provider_id"] == provider["id"]
    )
    runtime["name"] = "Microsoft Foundry example-project via APIM"
    runtime["config"].update(
        control_plane_managed=True,
        project_endpoint=(
            "https://example-foundry-resource.services.ai.azure.com/"
            "api/projects/example-project"
        ),
        backend_url=(
            "https://example-foundry-resource.services.ai.azure.com/"
            "api/projects/example-project"
        ),
        auth_strategy="managed_identity",
        managed_identity_resource="https://ai.azure.com",
        streaming_mode="native",
    )
    request = GatewayPublicationCreate(
        gateway_profile_id=APIM_ID,
        provider=ProviderTarget(existing_id=provider["id"]),
        runtime=RuntimeTarget(existing_id=runtime["id"]),
                model=ModelCreateTarget(deployment_name="claude-opus-5"),
    )

    publication = GatewayControlPlaneService(repository).publish(
        request, "owner@example.com"
    )
    binding = publication.desired_spec.bindings[-1]
    compiled = ApimPolicyCompiler().compile(publication)

    assert binding.api_format is ApiFormat.ANTHROPIC_MESSAGES
    assert binding.model.capabilities == [
        "chat",
        "tools",
        "vision",
        "reasoning",
        "streaming",
    ]
    assert str(binding.backend_url).rstrip("/") == (
        "https://example-foundry-resource.services.ai.azure.com"
    )
    assert binding.backend_path == "/anthropic/v1/messages"
    assert binding.runtime_config["api_format"] == "openai_chat"
    assert binding.model.model_key not in compiled.chat_completions_policy
    assert binding.model.model_key in compiled.messages_policy
    assert 'template="/anthropic/v1/messages"' in compiled.messages_policy
    assert 'name="anthropic-version" exists-action="override"' in compiled.messages_policy
    assert (
        'body[&quot;model&quot;] = &quot;claude-opus-5&quot;'
        in compiled.messages_policy
    )

def test_compiler_emits_native_apim_pool_breakers_retry_and_probe_routes() -> None:
    repository = InMemoryRepository()
    provider = next(
        item
        for item in repository.providers
        if item["brand_key"] == "microsoft_foundry"
    )
    runtime = next(
        item for item in repository.runtimes if item["provider_id"] == provider["id"]
    )
    primary_url = "https://primary.services.ai.azure.com/openai/v1"
    secondary_id = UUID("30000000-0000-4000-8000-000000000099")
    runtime["config"].update(
        control_plane_managed=True,
        project_endpoint="https://primary.services.ai.azure.com/api/projects/main",
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
            model=ModelCreateTarget(deployment_name="gpt-5.6-pool"),
        ),
        "owner@example.com",
    )
    binding = publication.desired_spec.bindings[-1]
    rate_limit = GatewayRateLimitResilience(
        max_attempts_per_request=2,
        retry_interval_seconds=1,
        first_fast_retry=True,
        circuit_breaker=GatewayRateLimitCircuitBreaker(
            failure_count=1,
            interval_seconds=60,
            trip_duration_seconds=60,
            accept_retry_after=True,
        ),
    )
    pool = GatewayBackendPoolConfig(
        members=[
            GatewayBackendPoolMember(
                runtime_id=runtime["id"],
                runtime_name=runtime["name"],
                backend_url=HttpUrl(primary_url),
                auth_strategy=AuthStrategy.MANAGED_IDENTITY,
                priority=0,
                weight=3,
            ),
            GatewayBackendPoolMember(
                runtime_id=secondary_id,
                runtime_name="Secondary Foundry runtime",
                backend_url=HttpUrl(
                    "https://secondary.services.ai.azure.com/openai/v1"
                ),
                auth_strategy=AuthStrategy.NAMED_VALUE_API_KEY,
                named_value_name="secondary-foundry-key",
                priority=0,
                weight=1,
            ),
        ],
        rate_limit=rate_limit,
    )
    pooled_binding = binding.model_copy(
        update={
            "runtime_config": {
                **binding.runtime_config,
                "apim_backend_pool": pool.model_dump(mode="json"),
            }
        }
    )
    pooled_publication = publication.model_copy(
        update={
            "desired_spec": publication.desired_spec.model_copy(
                update={"bindings": [pooled_binding]}
            )
        }
    )

    compiled = ApimPolicyCompiler(
        usage_observer_url="https://observer.example.com",
        usage_observer_key_named_value="turnstile-envoy-adapter-key",
    ).compile(pooled_publication)

    assert len(compiled.backends) == 3
    members = [
        item
        for item in compiled.backends
        if not isinstance(item, BackendPoolResource)
    ]
    pool_resource = next(
        item for item in compiled.backends if isinstance(item, BackendPoolResource)
    )
    assert len(members) == 2
    assert [member.priority for member in pool_resource.members] == [0, 0]
    assert [member.weight for member in pool_resource.members] == [3, 1]
    assert {member.backend_id for member in pool_resource.members} == {
        member.id for member in members
    }
    assert all(member.circuit_breaker is not None for member in members)
    assert {
        dict(member.headers)["x-turnstile-pool-runtime"] for member in members
    } == {runtime["name"], "Secondary Foundry runtime"}
    assert {
        dict(member.headers)["x-hive-runtime"] for member in members
    } == {runtime["name"], "Secondary Foundry runtime"}
    secondary = next(
        member for member in members if member.title.endswith("Secondary Foundry runtime")
    )
    assert dict(secondary.headers)["api-key"] == "{{secondary-foundry-key}}"
    assert {item.id for item in compiled.named_values} == {"secondary-foundry-key"}
    breaker = members[0].circuit_breaker
    assert breaker is not None
    assert breaker.failure_count == 1
    assert breaker.accept_retry_after is True
    assert breaker.status_code_ranges == ((429, 429), (408, 408), (500, 599))
    assert breaker.error_reasons == ("BackendConnectionFailure", "Timeout")
    policy = compiled.chat_completions_policy
    assert f'backend-id="{pool_resource.id}"' in policy
    assert all(f'backend-id="{member.id}"' in policy for member in members)
    assert 'name="selectedPoolProbeBackend"' in policy
    assert '&quot;x-turnstile-pool-member&quot;' in policy
    assert (
        '<retry condition="@(context.Response != null &amp;&amp; '
        'context.Response.StatusCode == 429)"' in policy
    )
    assert 'count="1"' in policy
    assert 'first-fast-retry="true"' in policy
    assert (
        '<forward-request timeout="120" buffer-request-body="true" '
        'buffer-response="false" />'
        in policy
    )
    retry_block = policy[policy.index("<retry condition=") : policy.index("</retry>")]
    assert "StatusCode == 429" in retry_block
    assert "StatusCode &gt;= 500" not in retry_block
    assert "BackendConnectionFailure" not in retry_block
    assert "Timeout" not in retry_block
    ElementTree.fromstring(policy)

def test_backend_pool_contract_rejects_duplicate_and_zero_weight_groups() -> None:
    runtime_id = UUID("30000000-0000-4000-8000-000000000001")
    rate_limit = GatewayRateLimitResilience(
        max_attempts_per_request=2,
        retry_interval_seconds=1,
        first_fast_retry=True,
        circuit_breaker=GatewayRateLimitCircuitBreaker(
            failure_count=1,
            interval_seconds=60,
            trip_duration_seconds=60,
            accept_retry_after=True,
        ),
    )
    with pytest.raises(ValueError, match="Input should be 1"):
        GatewayRateLimitCircuitBreaker.model_validate(
            {
                "failure_count": 2,
                "interval_seconds": 60,
                "trip_duration_seconds": 60,
                "accept_retry_after": True,
            }
        )
    with pytest.raises(ValueError, match="cannot repeat"):
        GatewayBackendPoolWrite(
            members=[
                GatewayBackendPoolMemberWrite(
                    runtime_id=runtime_id,
                    priority=0,
                    weight=1,
                ),
                GatewayBackendPoolMemberWrite(
                    runtime_id=runtime_id,
                    priority=0,
                    weight=1,
                ),
            ],
            rate_limit=rate_limit,
        )
    with pytest.raises(ValueError, match="positive weight"):
        GatewayBackendPoolWrite(
            members=[
                GatewayBackendPoolMemberWrite(
                    runtime_id=runtime_id,
                    priority=0,
                    weight=1,
                ),
                GatewayBackendPoolMemberWrite(
                    runtime_id=UUID("30000000-0000-4000-8000-000000000002"),
                    priority=1,
                    weight=0,
                ),
            ],
            rate_limit=rate_limit,
        )
    with pytest.raises(ValueError, match="requires 429, 408, and 500-599"):
        GatewayRateLimitCircuitBreaker(
            failure_count=1,
            interval_seconds=60,
            trip_duration_seconds=60,
            accept_retry_after=True,
            status_code_ranges=(
                GatewayBackendFailureStatusCodeRange(minimum=200, maximum=299),
            ),
        )
    with pytest.raises(ValueError, match="requires connection failure and timeout"):
        GatewayRateLimitCircuitBreaker(
            failure_count=1,
            interval_seconds=60,
            trip_duration_seconds=60,
            accept_retry_after=True,
            error_reasons=("Timeout",),
        )

def test_route_reconcile_does_not_copy_one_models_pool_to_sibling_models() -> None:
    repository = InMemoryRepository()
    provider = next(
        item
        for item in repository.providers
        if item["brand_key"] == "microsoft_foundry"
    )
    runtime = next(
        item for item in repository.runtimes if item["provider_id"] == provider["id"]
    )
    runtime["config"].update(
        control_plane_managed=True,
        project_endpoint="https://pool-template.services.ai.azure.com/api/projects/main",
        backend_url="https://pool-template.services.ai.azure.com/api/projects/main",
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
            model=ModelCreateTarget(deployment_name="gpt-pool-template"),
        ),
        "owner@example.com",
    )
    binding = publication.desired_spec.bindings[-1]
    assert binding.backend_url is not None
    pool = GatewayBackendPoolConfig(
        members=[
            GatewayBackendPoolMember(
                runtime_id=runtime["id"],
                runtime_name=runtime["name"],
                backend_url=binding.backend_url,
                priority=0,
                weight=1,
            ),
            GatewayBackendPoolMember(
                runtime_id=UUID("30000000-0000-4000-8000-000000000096"),
                runtime_name="Sibling-safe secondary",
                backend_url=HttpUrl(
                    "https://pool-template-secondary.services.ai.azure.com/"
                    "api/projects/main"
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
    pooled = binding.model_copy(
        update={
            "runtime_config": {
                **binding.runtime_config,
                "apim_backend_pool": pool.model_dump(mode="json"),
            }
        }
    )
    effective = publication.desired_spec.model_copy(update={"bindings": [pooled]})

    reconciled = GatewayControlPlaneService._reconciled_release_spec(
        effective,
        repository.registry(),
    )

    target = next(
        item
        for item in reconciled.bindings
        if item.model.model_key == binding.model.model_key
    )
    siblings = [
        item
        for item in reconciled.bindings
        if item.model.model_key != binding.model.model_key
    ]
    assert "apim_backend_pool" in target.runtime_config
    assert siblings
    assert all("apim_backend_pool" not in item.runtime_config for item in siblings)

def test_retry_backend_identity_changes_with_normalized_upstream() -> None:
    repository = InMemoryRepository()
    provider = next(
        item
        for item in repository.providers
        if item["brand_key"] == "microsoft_foundry"
    )
    runtime = next(
        item for item in repository.runtimes if item["provider_id"] == provider["id"]
    )
    project_endpoint = (
        "https://example-foundry-resource.services.ai.azure.com/"
        "api/projects/example-project"
    )
    runtime["config"].update(
        control_plane_managed=True,
        project_endpoint=project_endpoint,
        backend_url=project_endpoint,
        auth_strategy="managed_identity",
        managed_identity_resource="https://ai.azure.com",
        streaming_mode="native",
    )
    publication = GatewayControlPlaneService(repository).publish(
        GatewayPublicationCreate(
            gateway_profile_id=APIM_ID,
            provider=ProviderTarget(existing_id=provider["id"]),
            runtime=RuntimeTarget(existing_id=runtime["id"]),
            model=ModelCreateTarget(deployment_name="claude-opus-5"),
        ),
        "owner@example.com",
    )
    binding = publication.desired_spec.bindings[-1]
    legacy_binding = binding.model_copy(
        update={
            "api_format": ApiFormat.OPENAI_CHAT,
            "backend_url": project_endpoint,
            "backend_path": "/openai/v1/chat/completions",
        }
    )
    legacy_publication = publication.model_copy(
        update={
            "desired_spec": publication.desired_spec.model_copy(
                update={"bindings": [legacy_binding]}
            )
        }
    )
    compiler = ApimPolicyCompiler(
        usage_observer_url="https://observer.example.com",
        usage_observer_key_named_value="turnstile-envoy-adapter-key",
    )

    legacy_backend = compiler.compile(legacy_publication).backends[0]
    messages_backend = compiler.compile(publication).backends[0]

    assert legacy_backend.url == "https://observer.example.com/api/projects/example-project"
    assert messages_backend.url == "https://observer.example.com"
    assert legacy_backend.id != messages_backend.id
    assert compiler.compile(publication).backends[0].id == messages_backend.id

def test_retry_upgrades_legacy_failed_foundry_claude_binding() -> None:
    repository = InMemoryRepository()
    provider = next(
        item
        for item in repository.providers
        if item["brand_key"] == "microsoft_foundry"
    )
    runtime = next(
        item for item in repository.runtimes if item["provider_id"] == provider["id"]
    )
    project_endpoint = (
        "https://example-foundry-resource.services.ai.azure.com/"
        "api/projects/example-project"
    )
    runtime["config"].update(
        control_plane_managed=True,
        project_endpoint=project_endpoint,
        backend_url=project_endpoint,
        auth_strategy="managed_identity",
        managed_identity_resource="https://ai.azure.com",
        streaming_mode="native",
    )
    service = GatewayControlPlaneService(repository)
    publication = service.publish(
        GatewayPublicationCreate(
            gateway_profile_id=APIM_ID,
            provider=ProviderTarget(existing_id=provider["id"]),
            runtime=RuntimeTarget(existing_id=runtime["id"]),
            model=ModelCreateTarget(deployment_name="claude-opus-5"),
        ),
        "owner@example.com",
    )
    stored = next(
        item for item in repository.gateway_publications if item["id"] == publication.id
    )
    legacy_binding = stored["desired_spec"]["bindings"][-1]
    legacy_binding.update(
        api_format="openai_chat",
        backend_url=project_endpoint,
        backend_path="/openai/v1/chat/completions",
    )
    next(
        item
        for item in stored["desired_spec"]["discovery_models"]
        if item["id"] == legacy_binding["model"]["model_key"]
    )["api_format"] = "openai_chat"
    stored["desired_spec_sha256"] = "legacy-openai-spec"
    repository.transition_gateway_publication(
        publication.id,
        "queued",
        "failed",
        {"error_code": "max_attempts_exceeded"},
        "worker",
    )

    retried = service.retry(
        publication.id,
        GatewayPublicationRetry(),
        "owner@example.com",
    )
    binding = retried.desired_spec.bindings[-1]

    assert retried.id == publication.id
    assert retried.status == "queued"
    assert retried.desired_spec_sha256 != "legacy-openai-spec"
    assert binding.api_format is ApiFormat.ANTHROPIC_MESSAGES
    assert str(binding.backend_url).rstrip("/") == (
        "https://example-foundry-resource.services.ai.azure.com"
    )
    assert binding.backend_path == "/anthropic/v1/messages"

def test_external_foundry_claude_uses_anthropic_api_key_header() -> None:
    repository = InMemoryRepository()
    publication = GatewayControlPlaneService(repository).publish(
        external_tenant_foundry_publication(
            repository,
            deployment="claude-opus-5",
        ),
        "owner@example.com",
    )

    compiled = ApimPolicyCompiler().compile(publication)
    binding = publication.desired_spec.bindings[-1]

    assert binding.api_format is ApiFormat.ANTHROPIC_MESSAGES
    assert '<set-header name="x-api-key" exists-action="override">' in (
        compiled.messages_policy
    )
    assert '<set-header name="api-key" exists-action="override">' not in (
        compiled.messages_policy
    )
    assert 'name="anthropic-version" exists-action="override"' in (
        compiled.messages_policy
    )

def test_claude_first_foundry_activation_preserves_shared_connection_protocol() -> None:
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
    service = GatewayControlPlaneService(
        repository,
        apim_principal_id="39deeba0-9799-4806-96c4-2f65eb0f22d1",
    )
    publication = service.publish(
        foundry_publication("claude-opus-5"), "owner@example.com"
    )
    current = "queued"
    for status in (
        "validating",
        "provisioning",
        "building_revision",
        "verifying",
        "promoting",
    ):
        repository.transition_gateway_publication(
            publication.id, current, status, {}, "worker"
        )
        current = status

    repository.activate_gateway_publication(publication.id, "worker")

    runtime = next(
        item
        for item in repository.runtimes
        if item["name"] == "Microsoft Foundry contoso-ai/finops via APIM"
    )
    assert runtime["config"]["path"] == "/chat/completions"
    assert runtime["config"]["api_format"] == "openai_chat"
    assert runtime["config"]["backend_url"] == (
        "https://contoso-ai.services.ai.azure.com/api/projects/finops"
    )
    assert runtime["config"]["backend_path"] == "/openai/v1/chat/completions"
    model = next(
        model for model in repository.models if model["runtime_id"] == runtime["id"]
    )
    assert model["family_key"] == "claude"
    discovery = service._existing_discovery(APIM_ID, repository.registry())
    assert next(item for item in discovery if item.id == model["model_key"]).api_format is (
        ApiFormat.ANTHROPIC_MESSAGES
    )
    removal = service.remove_model(model["id"], "owner@example.com")
    assert removal.desired_spec.removed_models[0].api_format is (
        ApiFormat.ANTHROPIC_MESSAGES
    )

def test_external_tenant_foundry_key_is_encrypted_outside_the_release() -> None:
    repository = InMemoryRepository()
    cipher = CredentialCipher(Fernet.generate_key())

    publication = GatewayControlPlaneService(repository, cipher).publish(
        external_tenant_foundry_publication(repository), "owner@example.com"
    )
    binding = publication.desired_spec.bindings[-1]
    encrypted = repository.gateway_publication_credential(publication.id)

    assert str(binding.backend_url).rstrip("/") == (
        "https://admin-8382-resource.openai.azure.com"
    )
    assert binding.backend_path == "/openai/v1/chat/completions"
    assert binding.auth_strategy.value == "named_value_api_key"
    assert binding.named_value_name is not None
    assert binding.named_value_name.startswith("finops-foundry-key-")
    assert binding.managed_identity_resource is None
    assert binding.runtime_config["max_tokens_field"] == "max_completion_tokens"
    assert binding.runtime_config["supports_temperature"] is False
    assert binding.runtime_config["inference_endpoint"] == (
        "https://admin-8382-resource.openai.azure.com/openai/v1"
    )
    assert "authorization" not in binding.runtime_config
    assert encrypted is not None
    assert cipher.decrypt(encrypted) == "external-foundry-key"
    snapshot = publication.model_dump_json()
    assert "external-foundry-key" not in snapshot

def test_external_tenant_foundry_policy_uses_secret_api_key_header() -> None:
    repository = InMemoryRepository()
    publication = GatewayControlPlaneService(repository).publish(
        external_tenant_foundry_publication(repository), "owner@example.com"
    )

    compiled = ApimPolicyCompiler().compile(publication)
    policy = compiled.chat_completions_policy
    responses_policy = compiled.responses_policy
    binding = publication.desired_spec.bindings[-1]

    assert len(compiled.named_values) == 1
    assert compiled.named_values[0].id == binding.named_value_name
    assert '<set-header name="api-key" exists-action="override">' in policy
    assert f"{{{{{binding.named_value_name}}}}}" in policy
    assert "external-foundry-key" not in policy
    assert 'template="/openai/v1/chat/completions"' in policy
    assert f"{{{{{binding.named_value_name}}}}}" in responses_policy
    assert 'template="/openai/v1/responses"' in responses_policy
    assert 'body[&quot;model&quot;] = &quot;gpt-5.6-sol&quot;' in responses_policy
    assert 'template="/openai/v1/responses/compact"' in compiled.responses_compact_policy
    assert 'name="selectedContextWindow"' in compiled.responses_compact_policy
    assert '<set-header name="Accept-Encoding" exists-action="override">' in responses_policy
    assert "<value>identity</value>" in responses_policy
    assert "streamOptions" not in responses_policy

def test_usage_observer_preserves_foundry_route_auth_and_model_transform() -> None:
    repository = InMemoryRepository()
    publication = GatewayControlPlaneService(repository).publish(
        external_tenant_foundry_publication(repository), "owner@example.com"
    )

    compiled = ApimPolicyCompiler(
        usage_observer_url="https://observer.example.com",
        usage_observer_key_named_value="turnstile-envoy-adapter-key",
    ).compile(publication)
    policy = compiled.chat_completions_policy
    binding = publication.desired_spec.bindings[-1]
    backend = compiled.backends[-1]

    assert backend.id.startswith("turnstile-obs-")
    assert backend.url == "https://observer.example.com"
    assert backend.headers == (
        ("x-adapter-key", "{{turnstile-envoy-adapter-key}}"),
        ("x-turnstile-upstream-host", "admin-8382-resource.openai.azure.com"),
    )
    assert f"{{{{{binding.named_value_name}}}}}" in policy
    assert 'template="/openai/v1/chat/completions"' in policy
    assert 'body[&quot;model&quot;] = &quot;gpt-5.6-sol&quot;' in policy
    assert 'streamOptions[&quot;include_usage&quot;] = true' in policy
    assert 'name="selectedRoutingManaged" value="@(true)"' in policy
    assert "x-turnstile-api-format" in policy
    assert "openai_chat" in policy

def test_usage_observer_preserves_foundry_managed_identity_and_project_path() -> None:
    repository = InMemoryRepository()
    request = foundry_publication()
    provider = next(
        item
        for item in repository.providers
        if item["brand_key"] == "microsoft_foundry"
    )
    request.provider = ProviderTarget(existing_id=provider["id"])
    publication = GatewayControlPlaneService(
        repository,
        apim_principal_id="39deeba0-9799-4806-96c4-2f65eb0f22d1",
    ).publish(request, "owner@example.com")

    compiled = ApimPolicyCompiler(
        usage_observer_url="https://observer.example.com",
        usage_observer_key_named_value="turnstile-envoy-adapter-key",
    ).compile(publication)
    backend = compiled.backends[-1]

    assert backend.url == "https://observer.example.com/api/projects/finops"
    assert backend.headers == (
        ("x-adapter-key", "{{turnstile-envoy-adapter-key}}"),
        ("x-turnstile-upstream-host", "contoso-ai.services.ai.azure.com"),
    )
    assert (
        '<authentication-managed-identity resource="https://ai.azure.com" '
        'output-token-variable-name="turnstileProviderAccessToken" '
        'ignore-error="false" />'
        in compiled.chat_completions_policy
    )
    assert 'context.Variables["turnstileProviderAccessToken"]' in (
        compiled.chat_completions_policy
    )
    assert 'template="/openai/v1/chat/completions"' in compiled.chat_completions_policy
    assert (
        'body[&quot;model&quot;] = &quot;gpt-5-mini-deployment&quot;'
        in compiled.chat_completions_policy
    )

def test_usage_observer_keeps_same_name_cross_account_projects_separate() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(repository)
    first = service.publish(
        external_tenant_foundry_publication(
            repository,
            account="tenant-a-resource",
            project="shared",
            deployment="gpt-tenant-a",
        ),
        "owner@example.com",
    )
    current = "queued"
    for status in (
        "validating",
        "provisioning",
        "building_revision",
        "verifying",
        "promoting",
    ):
        assert repository.transition_gateway_publication(
            first.id, current, status, {}, "worker"
        )
        current = status
    repository.activate_gateway_publication(first.id, "worker")

    second = service.publish(
        external_tenant_foundry_publication(
            repository,
            account="tenant-b-resource",
            project="shared",
            deployment="gpt-tenant-b",
        ),
        "owner@example.com",
    )
    assert {binding.runtime_name for binding in second.desired_spec.bindings} == {
        "Microsoft Foundry tenant-a-resource/shared via APIM",
        "Microsoft Foundry tenant-b-resource/shared via APIM",
    }
    colliding_spec = second.desired_spec.model_copy(
        update={
            "bindings": [
                binding.model_copy(update={"runtime_name": "Renamed Foundry connection"})
                for binding in second.desired_spec.bindings
            ]
        }
    )
    colliding_publication = second.model_copy(update={"desired_spec": colliding_spec})
    compiled = ApimPolicyCompiler(
        usage_observer_url="https://observer.example.com",
        usage_observer_key_named_value="turnstile-envoy-adapter-key",
    ).compile(colliding_publication)

    upstream_hosts = {
        dict(backend.headers)["x-turnstile-upstream-host"]
        for backend in compiled.backends
    }
    assert upstream_hosts == {
        "tenant-a-resource.openai.azure.com",
        "tenant-b-resource.openai.azure.com",
    }

def test_usage_observer_preserves_bedrock_route_auth_and_body_transform() -> None:
    repository = InMemoryRepository()
    publication = GatewayControlPlaneService(repository).publish(
        bedrock_publication(), "owner@example.com"
    )

    compiled = ApimPolicyCompiler(
        usage_observer_url="https://observer.example.com",
        usage_observer_key_named_value="turnstile-envoy-adapter-key",
    ).compile(publication)
    policy = compiled.messages_policy
    binding = publication.desired_spec.bindings[-1]
    backend = compiled.backends[-1]

    assert backend.id.startswith("turnstile-obs-")
    assert backend.url == "https://observer.example.com"
    assert backend.headers == (
        ("x-adapter-key", "{{turnstile-envoy-adapter-key}}"),
        (
            "x-turnstile-upstream-host",
            "bedrock-runtime.ap-southeast-2.amazonaws.com",
        ),
    )
    assert f"Bearer {{{{{binding.named_value_name}}}}}" in policy
    assert 'template="/model/au.anthropic.claude-sonnet-4-6/invoke"' in policy
    assert 'body[&quot;anthropic_version&quot;] = &quot;bedrock-2023-05-31&quot;' in policy
    assert "x-turnstile-api-format" in policy
    assert "anthropic_messages" in policy
    assert 'name="selectedRoutingManaged" value="@(true)"' in policy

def test_unmanaged_known_model_is_marked_for_legacy_fallback() -> None:
    repository = InMemoryRepository()
    publication = GatewayControlPlaneService(repository).publish(
        bedrock_publication(), "owner@example.com"
    )
    unmanaged = publication.desired_spec.bindings[-1].model_copy(
        update={"routing_managed": False}
    )
    desired = publication.desired_spec.model_copy(update={"bindings": [unmanaged]})
    publication = publication.model_copy(update={"desired_spec": desired})

    compiled = ApimPolicyCompiler().compile(publication)

    assert 'name="selectedRoutingManaged" value="@(false)"' in compiled.messages_policy
    assert "<set-backend-service" not in compiled.messages_policy

def test_usage_observer_requires_a_complete_https_origin_configuration() -> None:
    with pytest.raises(ValueError, match="configured together"):
        ApimPolicyCompiler(usage_observer_url="https://observer.example.com")
    with pytest.raises(ValueError, match="HTTPS origin"):
        ApimPolicyCompiler(
            usage_observer_url="https://observer.example.com/path",
            usage_observer_key_named_value="turnstile-envoy-adapter-key",
        )

def test_external_tenant_key_is_materialized_once_then_erased() -> None:
    repository = InMemoryRepository()
    cipher = CredentialCipher(Fernet.generate_key())
    publication = GatewayControlPlaneService(repository, cipher).publish(
        external_tenant_foundry_publication(repository), "owner@example.com"
    )
    client = FakeApimClient()
    worker = GatewayPublicationWorker(repository, client, client.policy, cipher=cipher)

    worker.run_once("worker")
    provisioning = worker.run_once("worker")

    assert provisioning is not None
    assert provisioning.status == "provisioning"
    assert client.named_values[-1].value == "external-foundry-key"
    assert repository.gateway_publication_credential(publication.id) is None

def test_foundry_activation_normalizes_older_release_token_field() -> None:
    repository = InMemoryRepository()
    publication = GatewayControlPlaneService(repository).publish(
        external_tenant_foundry_publication(repository), "owner@example.com"
    )
    stored = next(
        item for item in repository.gateway_publications if item["id"] == publication.id
    )
    stored["desired_spec"]["bindings"][-1]["runtime_config"].pop(
        "max_tokens_field", None
    )
    current = "queued"
    for next_status in (
        "validating",
        "provisioning",
        "building_revision",
        "verifying",
        "promoting",
    ):
        repository.transition_gateway_publication(
            publication.id, current, next_status, {}, "worker"
        )
        current = next_status

    repository.activate_gateway_publication(publication.id, "worker")

    runtime = next(
        item
        for item in repository.runtimes
        if item["name"] == "Microsoft Foundry admin-8382-resource/admin-8382 via APIM"
    )
    assert runtime["config"]["max_tokens_field"] == "max_completion_tokens"
    assert runtime["config"]["supports_temperature"] is False

def test_external_tenant_foundry_failed_release_requires_replacement_key() -> None:
    repository = InMemoryRepository()
    cipher = CredentialCipher(Fernet.generate_key())
    service = GatewayControlPlaneService(repository, cipher)
    publication = service.publish(
        external_tenant_foundry_publication(repository), "owner@example.com"
    )
    repository.transition_gateway_publication(
        publication.id,
        "queued",
        "failed",
        {"error_code": "probe_failed", "error_message": "probe failed"},
        "worker",
    )

    with pytest.raises(ControlPlaneConflictError, match="replacement API key"):
        service.retry(publication.id, GatewayPublicationRetry(), "owner@example.com")

    retried = service.retry(
        publication.id,
        GatewayPublicationRetry(api_key=SecretStr("replacement-foundry-key")),
        "owner@example.com",
    )

    encrypted = repository.gateway_publication_credential(publication.id)
    assert retried.status == "queued"
    assert encrypted is not None
    assert cipher.decrypt(encrypted) == "replacement-foundry-key"

def test_external_tenant_foundry_key_can_rotate_through_a_candidate_release() -> None:
    repository = InMemoryRepository()
    cipher = CredentialCipher(Fernet.generate_key())
    service = GatewayControlPlaneService(repository, cipher)
    publication = service.publish(
        external_tenant_foundry_publication(repository), "owner@example.com"
    )
    current = "queued"
    for status in (
        "validating",
        "provisioning",
        "building_revision",
        "verifying",
        "promoting",
    ):
        assert repository.transition_gateway_publication(
            publication.id, current, status, {}, "worker"
        )
        current = status
    repository.activate_gateway_publication(publication.id, "worker")
    original = publication.desired_spec.bindings[-1]

    rotation = service.rotate_credential(
        APIM_ID,
        GatewayCredentialRotation(
            model_key=original.model.model_key,
            api_key=SecretStr("rotated-foundry-key"),
        ),
        "owner@example.com",
    )
    rotated = rotation.desired_spec.bindings[-1]
    encrypted = repository.gateway_publication_credential(rotation.id)
    policy = ApimPolicyCompiler().compile(rotation).chat_completions_policy

    assert rotation.publication_kind.value == "credential_rotation"
    assert rotated.auth_strategy.value == "named_value_api_key"
    assert rotated.named_value_name != original.named_value_name
    assert encrypted is not None
    assert cipher.decrypt(encrypted) == "rotated-foundry-key"
    assert f"{{{{{rotated.named_value_name}}}}}" in policy
    assert "rotated-foundry-key" not in policy

def test_foundry_chat_policy_owns_backend_auth_path_and_deployment_mapping() -> None:
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
    ).publish(foundry_publication(), "owner@example.com")

    compiled = ApimPolicyCompiler().compile(publication)
    policy = compiled.chat_completions_policy
    binding = publication.desired_spec.bindings[-1]

    assert len(compiled.backends) == 1
    assert compiled.backends[0].url == (
        "https://contoso-ai.services.ai.azure.com/api/projects/finops"
    )
    assert 'backend-id="turnstile-dyn-' in policy
    assert 'template="/openai/v1/chat/completions"' in policy
    assert 'authentication-managed-identity resource="https://ai.azure.com"' in policy
    assert 'output-token-variable-name="turnstileProviderAccessToken"' in policy
    assert 'context.Variables["turnstileProviderAccessToken"]' in policy
    assert 'body[&quot;model&quot;] = &quot;gpt-5-mini-deployment&quot;' in policy
    assert 'name="selectedProviderName" value="microsoft_foundry"' in policy
    assert binding.model.model_key not in compiled.messages_policy

def test_reusing_a_foundry_project_needs_only_a_deployment_name() -> None:
    repository = InMemoryRepository()
    foundry_provider = next(
        item for item in repository.providers if item["brand_key"] == "microsoft_foundry"
    )
    runtime = next(
        item for item in repository.runtimes if item["provider_id"] == foundry_provider["id"]
    )
    runtime["enabled"] = True
    runtime["config"] = {
        "control_plane_managed": True,
        "project_endpoint": (
            "https://contoso-ai.services.ai.azure.com/api/projects/finops"
        ),
        "backend_url": (
            "https://contoso-ai.services.ai.azure.com/api/projects/finops"
        ),
        "path": "/chat/completions",
        "api_format": "openai_chat",
        "auth_strategy": "managed_identity",
        "managed_identity_resource": "https://ai.azure.com",
        "streaming_mode": "native",
    }
    request = GatewayPublicationCreate(
        gateway_profile_id=APIM_ID,
        provider=ProviderTarget(existing_id=foundry_provider["id"]),
        runtime=RuntimeTarget(existing_id=runtime["id"]),
        model=ModelCreateTarget(deployment_name="gpt-5.4"),
    )

    publication = GatewayControlPlaneService(repository).publish(
        request, "owner@example.com"
    )
    binding = publication.desired_spec.bindings[-1]

    assert binding.runtime_id == runtime["id"]
    assert binding.model.upstream_model_id == "gpt-5.4"
    assert binding.model.display_name == "gpt-5.4 · Microsoft Foundry"
    assert binding.model.model_key.startswith("gpt-5.4-foundry-")
    assert binding.model.capabilities == ["chat", "tools", "streaming"]
    assert binding.backend_path == "/openai/v1/chat/completions"
