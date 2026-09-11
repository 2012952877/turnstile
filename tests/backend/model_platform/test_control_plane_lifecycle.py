from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID
from xml.etree import ElementTree

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import HttpUrl, SecretStr

from tests.backend.model_platform.control_plane_support import (
    APIM_ID,
    FakeApimClient,
    GatewayControlPlaneService,
    GatewayPublicationWorker,
    StubTokenProvider,
    bedrock_publication,
    publisher_settings,
)
from turnstile_core.domain.control_plane import (
    GatewayCredentialRotation,
    GatewayPublicationCreate,
    ModelCreateTarget,
    RuntimeTarget,
)
from turnstile_core.domain.runtime_models import ProviderTarget
from turnstile_core.integrations.apim_control_plane import (
    ApimPolicyCompiler,
    AzureApimPublisherClient,
)
from turnstile_core.persistence.in_memory import InMemoryRepository
from turnstile_core.security import CredentialCipher
from turnstile_core.services.control_plane import (
    ControlPlaneConflictError,
)


def test_openai_publication_worker_activates_only_after_verification() -> None:
    repository = InMemoryRepository()
    cipher = CredentialCipher(Fernet.generate_key())
    original_default = [model["id"] for model in repository.models if model["is_default"]]
    publication = GatewayControlPlaneService(repository, cipher).publish(
        GatewayPublicationCreate(
            gateway_profile_id=APIM_ID,
            provider=ProviderTarget(template="openai_compatible"),
            runtime=RuntimeTarget(
                openai_base_url=HttpUrl("https://api.example.test/v1"),
                api_key=SecretStr("one-time-compatible-key"),
            ),
            model=ModelCreateTarget(
                model_key="compatible-chat",
                display_name="Compatible chat",
                upstream_model_id="chat-v1",
            ),
        ),
        "owner@example.com",
    )
    client = FakeApimClient()
    worker = GatewayPublicationWorker(repository, client, client.policy, cipher=cipher)
    for expected in (
        "validating", "provisioning", "building_revision", "verifying", "promoting", "active",
    ):
        result = worker.run_once("compatible-worker")
        assert result is not None and result.status.value == expected
        if expected != "active":
            assert not any(model["model_key"] == "compatible-chat" for model in repository.models)
        if expected != "validating":
            assert repository.gateway_publication_credential(publication.id) is None
    assert repository.effective_gateway_releases[APIM_ID] == publication.id
    model = next(model for model in repository.models if model["model_key"] == "compatible-chat")
    runtime = next(item for item in repository.runtimes if item["id"] == model["runtime_id"])
    assert runtime["config"]["backend_path"] == "/v1/chat/completions"
    assert runtime["config"]["max_tokens_field"] == "max_tokens"
    assert runtime["config"]["credential_kind"] == "api_key"
    assert runtime["config"].get("credential_provisioned") is not False
    assert result is not None
    named_values = result.resource_manifest.get("named_values")
    assert isinstance(named_values, list)
    assert runtime["config"]["named_value_name"] in named_values
    assert model["assignment_required"] is True
    assert [model["id"] for model in repository.models if model["is_default"]] == original_default


def test_publication_is_a_draft_until_apim_promotion() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(repository)

    publication = service.publish(bedrock_publication(), "owner@example.com")

    assert publication.status == "queued"
    assert publication.desired_spec.bindings[-1].model.assignment_required is True
    assert all(
        model["model_key"] != "claude-sonnet-4-6-bedrock" for model in repository.models
    )

def test_publication_api_key_is_encrypted_outside_the_release_snapshot() -> None:
    repository = InMemoryRepository()
    cipher = CredentialCipher(Fernet.generate_key())
    request = bedrock_publication()
    request.runtime.api_key = SecretStr("bedrock-key-must-not-leak")

    publication = GatewayControlPlaneService(repository, cipher).publish(
        request, "owner@example.com"
    )
    encrypted = repository.gateway_publication_credential(publication.id)

    assert encrypted is not None
    assert encrypted != b"bedrock-key-must-not-leak"
    assert cipher.decrypt(encrypted) == "bedrock-key-must-not-leak"
    assert "bedrock-key-must-not-leak" not in publication.model_dump_json()
    assert "api_key" not in publication.model_dump_json()

def test_worker_erases_the_api_key_after_named_value_provisioning() -> None:
    repository = InMemoryRepository()
    cipher = CredentialCipher(Fernet.generate_key())
    request = bedrock_publication()
    request.runtime.api_key = SecretStr("one-time-bedrock-key")
    publication = GatewayControlPlaneService(repository, cipher).publish(
        request, "owner@example.com"
    )
    client = FakeApimClient()
    worker = GatewayPublicationWorker(
        repository,
        client,
        client.policy,
        cipher=cipher,
    )

    validating = worker.run_once("worker")
    assert validating is not None
    assert validating.status == "validating"
    assert repository.gateway_publication_credential(publication.id) is not None
    provisioning = worker.run_once("worker")
    assert provisioning is not None
    assert provisioning.status == "provisioning"

    assert client.named_values[-1].value == "one-time-bedrock-key"
    assert repository.gateway_publication_credential(publication.id) is None

def test_terminal_failure_erases_the_publication_api_key() -> None:
    repository = InMemoryRepository()
    cipher = CredentialCipher(Fernet.generate_key())
    request = bedrock_publication()
    request.runtime.api_key = SecretStr("failed-publication-key")
    publication = GatewayControlPlaneService(repository, cipher).publish(
        request, "owner@example.com"
    )

    repository.transition_gateway_publication(
        publication.id,
        "queued",
        "failed",
        {"error_code": "test", "error_message": "test"},
        "worker",
    )

    assert repository.gateway_publication_credential(publication.id) is None

def test_runtime_url_model_id_and_key_generate_the_bedrock_configuration() -> None:
    repository = InMemoryRepository()
    cipher = CredentialCipher(Fernet.generate_key())
    request = bedrock_publication()
    request.runtime = RuntimeTarget(
        bedrock_runtime_url=HttpUrl(
            "https://bedrock-runtime.ap-southeast-2.amazonaws.com"
        ),
        api_key=SecretStr("bedrock-api-key"),
    )

    publication = GatewayControlPlaneService(repository, cipher).publish(
        request, "owner@example.com"
    )
    binding = publication.desired_spec.bindings[-1]

    assert binding.runtime_name == "Amazon Bedrock Claude (ap-southeast-2) via APIM"
    assert str(binding.backend_url).rstrip("/") == (
        "https://bedrock-runtime.ap-southeast-2.amazonaws.com"
    )
    assert binding.backend_path == "/model/{upstream_model_id}/invoke"
    assert binding.named_value_name is not None
    assert binding.named_value_name.startswith("finops-bedrock-")
    assert binding.runtime_config["region"] == "ap-southeast-2"
    assert "api_key" not in publication.model_dump_json()

def test_bedrock_endpoint_cannot_name_a_different_model() -> None:
    request = bedrock_publication()
    request.runtime = RuntimeTarget(
        bedrock_runtime_url=HttpUrl(
            "https://bedrock-runtime.ap-southeast-2.amazonaws.com/"
            "model/au.anthropic.claude-opus-5/invoke"
        ),
        api_key=SecretStr("bedrock-api-key"),
    )

    with pytest.raises(ControlPlaneConflictError, match="regional origin"):
        GatewayControlPlaneService(
            InMemoryRepository(), CredentialCipher(Fernet.generate_key())
        ).publish(request, "owner@example.com")

def test_automated_anthropic_publication_rejects_a_non_claude_model() -> None:
    request = bedrock_publication()
    request.model = ModelCreateTarget(
        model_key="unrelated-model",
        display_name="Unrelated Model",
        upstream_model_id="vendor.unrelated-model-v1",
    )

    with pytest.raises(ControlPlaneConflictError, match="requires a Claude model"):
        GatewayControlPlaneService(InMemoryRepository()).publish(
            request, "owner@example.com"
        )

def test_new_bedrock_connection_requires_an_api_key() -> None:
    with pytest.raises(ValueError, match="requires its Runtime URL and API key"):
        RuntimeTarget(
            bedrock_runtime_url=HttpUrl(
                "https://bedrock-runtime.ap-southeast-2.amazonaws.com"
            )
        )

def test_active_bedrock_runtime_must_be_reused_instead_of_recreated_in_the_same_region() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(repository)
    publication = service.publish(bedrock_publication(), "owner@example.com")
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
    provider = next(
        item for item in repository.providers if item["brand_key"] == "amazon_bedrock"
    )
    request = bedrock_publication("claude-opus-bedrock")
    request.provider = ProviderTarget(existing_id=provider["id"])
    request.runtime = RuntimeTarget(
        bedrock_runtime_url=HttpUrl(
            "https://bedrock-runtime.ap-southeast-2.amazonaws.com"
        ),
        api_key=SecretStr("replacement-key"),
    )

    with pytest.raises(ControlPlaneConflictError, match="gateway and Region"):
        service.publish(request, "owner@example.com")

def test_a_different_bedrock_region_creates_an_independent_runtime() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(repository)
    first = service.publish(bedrock_publication(), "owner@example.com")
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
    provider = next(
        item for item in repository.providers if item["brand_key"] == "amazon_bedrock"
    )
    request = bedrock_publication("claude-opus-4-8-bedrock")
    request.provider = ProviderTarget(existing_id=provider["id"])
    request.runtime = RuntimeTarget(
        bedrock_runtime_url=HttpUrl(
            "https://bedrock-runtime.us-east-1.amazonaws.com"
        ),
        api_key=SecretStr("us-east-1-key"),
    )
    request.model = ModelCreateTarget(
        model_key="claude-opus-4-8-bedrock",
        display_name="Claude Opus 4.8 - Amazon Bedrock",
        upstream_model_id="global.anthropic.claude-opus-4-8",
    )

    second = service.publish(request, "owner@example.com")
    binding = second.desired_spec.bindings[-1]

    assert binding.runtime_id is None
    assert binding.runtime_name == "Amazon Bedrock Claude (us-east-1) via APIM"
    assert str(binding.backend_url).rstrip("/") == (
        "https://bedrock-runtime.us-east-1.amazonaws.com"
    )
    assert binding.runtime_config["region"] == "us-east-1"
    assert binding.named_value_name != first.desired_spec.bindings[-1].named_value_name

def test_identical_publication_is_idempotent_but_a_different_one_waits() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(repository)

    first = service.publish(bedrock_publication(), "owner@example.com")
    repeated = service.publish(bedrock_publication(), "owner@example.com")
    assert repeated.id == first.id

    with pytest.raises(ControlPlaneConflictError, match="already in progress"):
        service.publish(bedrock_publication("claude-sonnet-4-7-bedrock"), "owner@example.com")

def test_resubmitting_a_failed_publication_requeues_it_with_a_fresh_key() -> None:
    repository = InMemoryRepository()
    cipher = CredentialCipher(Fernet.generate_key())
    request = bedrock_publication()
    request.runtime.api_key = SecretStr("first-key")
    service = GatewayControlPlaneService(repository, cipher)
    publication = service.publish(request, "owner@example.com")
    repository.transition_gateway_publication(
        publication.id,
        "queued",
        "failed",
        {"error_code": "test", "error_message": "test"},
        "worker",
    )
    request.runtime.api_key = SecretStr("replacement-key")

    retried = service.publish(request, "owner@example.com")

    assert retried.id == publication.id
    assert retried.status == "queued"
    assert retried.error_code is None
    encrypted = repository.gateway_publication_credential(publication.id)
    assert encrypted is not None
    assert cipher.decrypt(encrypted) == "replacement-key"

def test_existing_runtime_must_belong_to_the_selected_provider() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(repository)
    request = bedrock_publication()
    github = next(item for item in repository.providers if item["name"] == "GitHub Copilot")
    foundry = next(
        item for item in repository.runtimes if item["name"] == "Microsoft Foundry via APIM"
    )
    request.provider = ProviderTarget(existing_id=github["id"])
    request.runtime = RuntimeTarget(existing_id=foundry["id"])

    with pytest.raises(ControlPlaneConflictError, match="does not belong"):
        service.publish(request, "owner@example.com")

def test_outbox_lease_prevents_two_workers_from_claiming_the_same_release() -> None:
    repository = InMemoryRepository()
    publication = GatewayControlPlaneService(repository).publish(
        bedrock_publication(), "owner@example.com"
    )

    claimed = repository.claim_gateway_publication("worker-a", 60)
    assert claimed is not None and claimed["id"] == publication.id
    assert repository.claim_gateway_publication("worker-b", 60) is None

def test_outbox_does_not_claim_work_before_available_at() -> None:
    repository = InMemoryRepository()
    GatewayControlPlaneService(repository).publish(
        bedrock_publication(), "owner@example.com"
    )
    repository.gateway_publication_outbox[0]["available_at"] = (
        datetime.now(UTC) + timedelta(minutes=5)
    )

    assert repository.claim_gateway_publication("worker", 60) is None

def test_unstarted_publication_can_be_cancelled_without_worker_or_apim() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(repository)
    publication = service.publish(bedrock_publication(), "owner@example.com")

    cancelled = service.cancel_authorization(publication.id, "owner@example.com")

    assert cancelled.status == "rolled_back"
    assert cancelled.attempt_count == 0
    assert cancelled.started_at is None
    assert cancelled.error_code == "cancelled_by_owner"
    assert cancelled.error_message == "Publication cancelled before processing"
    assert repository.gateway_publication_outbox[0]["status"] == "completed"
    assert repository.claim_gateway_publication("worker", 60) is None
    assert repository.gateway_publication_audit[-1]["detail"] == {
        "error_code": "cancelled_by_owner",
        "error_message": "Publication cancelled before processing",
    }

def test_claimed_publication_cannot_be_cancelled_as_unstarted() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(repository)
    publication = service.publish(bedrock_publication(), "owner@example.com")
    assert repository.claim_gateway_publication("worker", 60) is not None

    with pytest.raises(ControlPlaneConflictError, match="unstarted queued"):
        service.cancel_authorization(publication.id, "owner@example.com")

    current = service.publication(publication.id)
    assert current.status == "queued"
    assert current.attempt_count == 1
    assert current.started_at is not None

def test_only_promoted_release_materializes_the_active_registry() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(repository)
    publication = service.publish(bedrock_publication(), "owner@example.com")

    with pytest.raises(ValueError, match="Only a promoted"):
        repository.activate_gateway_publication(publication.id, "worker")

    statuses = [
        "validating",
        "provisioning",
        "building_revision",
        "verifying",
        "promoting",
    ]
    current = "queued"
    for status in statuses:
        assert repository.transition_gateway_publication(
            publication.id, current, status, {}, "worker"
        )
        current = status

    active = repository.activate_gateway_publication(publication.id, "worker")
    model = next(
        item for item in repository.models if item["model_key"] == "claude-sonnet-4-6-bedrock"
    )

    assert active["status"] == "active"
    assert model["assignment_required"] is True
    assert model["publication_id"] == publication.id
    assert repository.effective_gateway_releases[APIM_ID] == publication.id

def test_bedrock_model_removal_is_physical_only_after_apim_promotion() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(repository)
    addition = service.publish(bedrock_publication(), "owner@example.com")
    current = "queued"
    for status in (
        "validating",
        "provisioning",
        "building_revision",
        "verifying",
        "promoting",
    ):
        assert repository.transition_gateway_publication(
            addition.id, current, status, {}, "worker"
        )
        current = status
    repository.activate_gateway_publication(addition.id, "worker")
    model = next(
        item
        for item in repository.models
        if item["model_key"] == "claude-sonnet-4-6-bedrock"
    )
    provider_id = model["provider_id"]
    runtime_id = model["runtime_id"]
    runtime = next(item for item in repository.runtimes if item["id"] == runtime_id)

    assert runtime["config"]["path"] == "/v1/messages"
    assert runtime["config"]["backend_path"] == "/model/{upstream_model_id}/invoke"

    removal = service.remove_model(model["id"], "owner@example.com")

    assert removal.publication_kind.value == "model_remove"
    assert any(item["id"] == model["id"] for item in repository.models)
    assert removal.desired_spec.removed_models[0].model_id == model["id"]
    assert all(
        item.id != model["model_key"]
        for item in removal.desired_spec.discovery_models
    )
    assert all(
        item.model.model_key != model["model_key"]
        for item in removal.desired_spec.bindings
    )
    compiled = ApimPolicyCompiler().compile(removal)
    assert model["model_key"] not in compiled.messages_policy
    assert model["model_key"] not in compiled.models_policy
    messages_root = ElementTree.fromstring(compiled.messages_policy)
    assert all(list(choose) for choose in messages_root.iter("choose"))
    assert "The requested model is not published" in compiled.messages_policy

    current = "queued"
    for status in (
        "validating",
        "provisioning",
        "building_revision",
        "verifying",
        "promoting",
    ):
        assert repository.transition_gateway_publication(
            removal.id, current, status, {}, "worker"
        )
        current = status
    repository.activate_gateway_publication(removal.id, "worker")

    assert all(item["id"] != model["id"] for item in repository.models)
    assert any(item["id"] == provider_id for item in repository.providers)
    assert any(item["id"] == runtime_id for item in repository.runtimes)
    assert repository.effective_gateway_releases[APIM_ID] == removal.id
    identities = repository.model_identities()
    assert identities[str(model["id"])].display_name == model["display_name"]
    assert identities[model["model_key"]].model_id == str(model["id"])

def test_removed_bedrock_model_can_be_readded_repeatedly() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(repository)

    def activate(publication_id: UUID) -> None:
        current = "queued"
        for status in (
            "validating",
            "provisioning",
            "building_revision",
            "verifying",
            "promoting",
        ):
            assert repository.transition_gateway_publication(
                publication_id, current, status, {}, "worker"
            )
            current = status
        repository.activate_gateway_publication(publication_id, "worker")

    first_addition = service.publish(bedrock_publication(), "owner@example.com")
    activate(first_addition.id)
    model = next(
        item
        for item in repository.models
        if item["model_key"] == "claude-sonnet-4-6-bedrock"
    )
    provider_id = model["provider_id"]
    runtime_id = model["runtime_id"]

    first_removal = service.remove_model(model["id"], "owner@example.com")
    activate(first_removal.id)

    repeat_request = GatewayPublicationCreate(
        gateway_profile_id=APIM_ID,
        provider=ProviderTarget(existing_id=provider_id),
        runtime=RuntimeTarget(existing_id=runtime_id),
        model=bedrock_publication().model,
    )
    second_addition = service.publish(repeat_request, "owner@example.com")
    assert second_addition.status.value == "queued"
    activate(second_addition.id)
    restored = next(
        item
        for item in repository.models
        if item["model_key"] == "claude-sonnet-4-6-bedrock"
    )
    assert restored["publication_id"] == second_addition.id

    second_removal = service.remove_model(restored["id"], "owner@example.com")
    activate(second_removal.id)
    third_addition = service.publish(repeat_request, "owner@example.com")

    assert third_addition.status.value == "queued"
    assert third_addition.id != second_addition.id

def test_foundry_model_removal_updates_shared_gateway_allowlist_before_deleting_row() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(repository)
    model = next(
        item for item in repository.models if item["model_key"] == "gpt-5.6-luna"
    )
    repository.user_model_policies["delete.user@contoso.com"] = {
        "user_id": "delete.user@contoso.com",
        "model_ids": [model["id"]],
        "updated_at": datetime.now(UTC),
        "updated_by": "setup",
    }
    repository.assistant_setting["model_id"] = model["id"]

    removal = service.remove_model(model["id"], "owner@example.com")

    assert any(item["id"] == model["id"] for item in repository.models)
    assert removal.desired_spec.bindings == []
    assert all(
        item.id != model["model_key"]
        for item in removal.desired_spec.discovery_models
    )
    assert {
        item.api_format.value for item in removal.desired_spec.discovery_models
    } == {"openai_chat"}
    compiled = ApimPolicyCompiler().compile(removal)
    assert model["model_key"] not in compiled.chat_completions_policy
    assert model["model_key"] not in compiled.models_policy
    assert "gpt-5.3-chat" in compiled.chat_completions_policy

    current = "queued"
    for status in (
        "validating",
        "provisioning",
        "building_revision",
        "verifying",
        "promoting",
    ):
        assert repository.transition_gateway_publication(
            removal.id, current, status, {}, "worker"
        )
        current = status
    repository.activate_gateway_publication(removal.id, "worker")

    assert all(item["id"] != model["id"] for item in repository.models)
    assert repository.user_model_policies["delete.user@contoso.com"]["model_ids"] == []
    audit = repository.user_model_access_audit[0]
    assert audit["previous_model_ids"] == [model["id"]]
    assert audit["new_model_ids"] == []
    assert repository.assistant_setting["model_id"] is None

def test_default_and_non_apim_models_cannot_be_deleted() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(repository)
    default_model = next(
        item for item in repository.models if item["model_key"] == "gpt-5.6-luna"
    )
    default_model["is_default"] = True
    direct_model = next(item for item in repository.models if item["model_key"] == "gpt-5.3-chat")
    direct_runtime = next(
        item for item in repository.runtimes if item["id"] == direct_model["runtime_id"]
    )
    direct_runtime["gateway_profile_id"] = None

    with pytest.raises(ControlPlaneConflictError, match="another default"):
        service.remove_model(default_model["id"], "owner@example.com")
    with pytest.raises(ControlPlaneConflictError, match="APIM gateway"):
        service.remove_model(direct_model["id"], "owner@example.com")

def test_failed_model_removal_requeues_without_api_key_even_if_gateway_is_disabled() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(repository)
    model = next(
        item for item in repository.models if item["model_key"] == "gpt-5.6-luna"
    )
    removal = service.remove_model(model["id"], "owner@example.com")
    assert repository.transition_gateway_publication(
        removal.id,
        "queued",
        "failed",
        {"error_code": "test", "error_message": "test"},
        "worker",
    )
    next(item for item in repository.gateways if item["id"] == APIM_ID)[
        "enabled"
    ] = False

    retried = service.remove_model(model["id"], "owner@example.com")

    assert retried.id == removal.id
    assert retried.status.value == "queued"
    assert repository.gateway_publication_credential(removal.id) is None

def test_model_cannot_be_edited_while_removal_is_in_flight() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(repository)
    model = next(
        item for item in repository.models if item["model_key"] == "gpt-5.6-luna"
    )
    service.remove_model(model["id"], "owner@example.com")

    with pytest.raises(ValueError, match="deletion is already in progress"):
        repository.update_registry_item(
            "model",
            model["id"],
            {"display_name": "Changed during deletion"},
        )

def test_second_model_reuses_dynamic_runtime_without_falling_back_to_databricks() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(repository)
    first = service.publish(bedrock_publication(), "owner@example.com")
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
    provider = next(
        item for item in repository.providers if item["brand_key"] == "amazon_bedrock"
    )
    runtime = next(
        item for item in repository.runtimes if item["provider_id"] == provider["id"]
    )
    runtime["config"].pop("backend_path")
    runtime["config"]["path"] = "/v1/messages"

    request = bedrock_publication("claude-opus-bedrock-test")
    request.provider = ProviderTarget(existing_id=provider["id"])
    request.runtime = RuntimeTarget(existing_id=runtime["id"])
    second = service.publish(request, "owner@example.com")
    compiled = ApimPolicyCompiler().compile(second)

    assert second.desired_spec.bindings[-1].routing_managed is True
    assert second.desired_spec.bindings[-1].backend_path == "/model/{upstream_model_id}/invoke"
    assert len(compiled.backends) == 1
    assert compiled.messages_policy.count(
        f'backend-id="{compiled.backends[0].id}"'
    ) == 2
    assert "claude-sonnet-4-6-bedrock" in compiled.messages_policy
    assert "claude-opus-bedrock-test" in compiled.messages_policy

def test_route_reconcile_rebuilds_effective_release_without_registry_mutation() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(repository)
    initial = service.publish(bedrock_publication(), "owner@example.com")
    current = "queued"
    for status in (
        "validating",
        "provisioning",
        "building_revision",
        "verifying",
        "promoting",
    ):
        assert repository.transition_gateway_publication(
            initial.id, current, status, {}, "worker"
        )
        current = status
    repository.activate_gateway_publication(initial.id, "worker")
    model_keys_before = sorted(model["model_key"] for model in repository.models)

    with pytest.raises(ValueError, match="effective gateway release changed"):
        repository.create_gateway_publication(
            APIM_ID,
            initial.desired_spec.model_dump(mode="json"),
            "stale-route-reconcile",
            "route_reconcile",
            "owner@example.com",
            expected_base_release_id=UUID("00000000-0000-4000-8000-000000000099"),
        )

    reconcile = service.reconcile_gateway(APIM_ID, "owner@example.com")

    assert reconcile.publication_kind.value == "route_reconcile"
    assert reconcile.desired_spec == initial.desired_spec
    client = FakeApimClient()
    worker = GatewayPublicationWorker(
        repository,
        client,
        client.policy,
        compiler=ApimPolicyCompiler(
            usage_observer_url="https://observer.example.com",
            usage_observer_key_named_value="turnstile-envoy-adapter-key",
        ),
    )
    result = None
    for _ in range(6):
        result = worker.run_once("worker")

    assert result is not None
    assert result.status.value == "active"
    assert repository.effective_gateway_releases[APIM_ID] == reconcile.id
    assert sorted(model["model_key"] for model in repository.models) == model_keys_before
    backend_calls = [value for kind, value in client.calls if kind == "backend"]
    assert backend_calls
    assert all(value.startswith("turnstile-obs-") for value in backend_calls)

def test_route_reconcile_adopts_and_probes_existing_managed_runtime_models() -> None:
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
    initial = service.publish(
        GatewayPublicationCreate(
            gateway_profile_id=APIM_ID,
            provider=ProviderTarget(existing_id=provider["id"]),
            runtime=RuntimeTarget(existing_id=runtime["id"]),
            model=ModelCreateTarget(deployment_name="gpt-chat-latest"),
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
            initial.id, current, status, {}, "worker"
        )
        current = status
    repository.activate_gateway_publication(initial.id, "worker")

    reconcile = service.reconcile_gateway(APIM_ID, "owner@example.com")
    expected_models = {
        model["model_key"]
        for model in repository.models
        if model["enabled"] and model["runtime_id"] == runtime["id"]
    }
    bindings = {
        binding.model.model_key: binding
        for binding in reconcile.desired_spec.bindings
        if binding.runtime_id == runtime["id"]
    }

    assert set(bindings) == expected_models
    assert bindings["gpt-5.6-luna"].model.assignment_required is False
    assert initial.desired_spec.bindings[-1].model.assignment_required is True
    compiled = ApimPolicyCompiler().compile(reconcile)
    assert compiled.responses_policy.count(
        'name="selectedRequiresAssignment" value="@(false)"'
    ) == 5
    for alias in expected_models:
        assert alias in compiled.responses_policy
        assert alias in compiled.responses_compact_policy

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": item.id}
                        for item in reconcile.desired_spec.discovery_models
                    ]
                },
            )
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

    regression_alias = initial.desired_spec.bindings[-1].model.model_key
    settings = publisher_settings().model_copy(
        update={
            "apim_probe_subscription_key": SecretStr("probe-key"),
            "apim_regression_model_key": regression_alias,
        }
    )
    client = AzureApimPublisherClient(
        settings,
        StubTokenProvider(),  # type: ignore[arg-type]
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.probe_revision("turnstile-test", reconcile)

    response_models = {
        json.loads(request.content)["model"]
        for request in requests
        if request.url.path.endswith("/responses")
    }
    compact_models = {
        json.loads(request.content)["model"]
        for request in requests
        if request.url.path.endswith("/responses/compact")
    }
    assert response_models == expected_models
    assert compact_models == expected_models

    stored = next(
        item for item in repository.gateway_publications if item["id"] == reconcile.id
    )
    stored["desired_spec"] = initial.desired_spec.model_dump(mode="json")
    fake_client = FakeApimClient()
    worker = GatewayPublicationWorker(repository, fake_client, fake_client.policy)
    for _ in range(6):
        worker.run_once("worker")

    for alias in expected_models:
        assert alias in fake_client.operation_policies["responses"]
        assert alias in fake_client.operation_policies["responses-compact"]

    follow_up = service.publish(
        GatewayPublicationCreate(
            gateway_profile_id=APIM_ID,
            provider=ProviderTarget(existing_id=provider["id"]),
            runtime=RuntimeTarget(existing_id=runtime["id"]),
            model=ModelCreateTarget(deployment_name="routing-model"),
        ),
        "owner@example.com",
    )
    follow_up_aliases = {
        binding.model.model_key for binding in follow_up.desired_spec.bindings
    }

    assert follow_up.base_release_id == reconcile.id
    assert expected_models <= follow_up_aliases
    compiled_follow_up = ApimPolicyCompiler().compile(follow_up)
    for alias in expected_models:
        assert alias in compiled_follow_up.responses_policy
        assert alias in compiled_follow_up.responses_compact_policy

    stored_follow_up = next(
        item for item in repository.gateway_publications if item["id"] == follow_up.id
    )
    latest_binding = stored_follow_up["desired_spec"]["bindings"][-1]
    stored_follow_up["desired_spec"] = {
        **initial.desired_spec.model_dump(mode="json"),
        "discovery_models": follow_up.desired_spec.model_dump(mode="json")[
            "discovery_models"
        ],
        "bindings": [
            *initial.desired_spec.model_dump(mode="json")["bindings"],
            latest_binding,
        ],
    }
    legacy_client = FakeApimClient()
    legacy_worker = GatewayPublicationWorker(
        repository, legacy_client, legacy_client.policy
    )
    for _ in range(6):
        legacy_worker.run_once("worker")

    for alias in expected_models:
        assert alias in legacy_client.operation_policies["responses"]
        assert alias in legacy_client.operation_policies["responses-compact"]

def test_unknown_managed_runtime_without_backend_path_is_rejected() -> None:
    repository = InMemoryRepository()
    provider_id = UUID("20000000-0000-4000-8000-000000000099")
    runtime_id = UUID("30000000-0000-4000-8000-000000000099")
    repository.providers.append(
        {
            "id": provider_id,
            "name": "Legacy Databricks",
            "provider_kind": "anthropic",
            "brand_key": "azure_databricks",
            "enabled": True,
            "config": {},
        }
    )
    repository.runtimes.append(
        {
            "id": runtime_id,
            "provider_id": provider_id,
            "gateway_profile_id": APIM_ID,
            "name": "Legacy Databricks via APIM",
            "runtime_kind": "openai_compatible",
            "brand_key": "azure_databricks",
            "enabled": True,
            "config": {
                "control_plane_managed": True,
                "api_format": "anthropic_messages",
                "path": "/v1/messages",
            },
        }
    )
    request = bedrock_publication("legacy-databricks-model")
    request.provider = ProviderTarget(existing_id=provider_id)
    request.runtime = RuntimeTarget(existing_id=runtime_id)

    with pytest.raises(ControlPlaneConflictError, match="provider backend path"):
        GatewayControlPlaneService(repository).publish(request, "owner@example.com")

def test_effective_release_survives_more_than_one_hundred_newer_failures() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(repository)
    first = service.publish(bedrock_publication(), "owner@example.com")
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
    provider = next(
        item for item in repository.providers if item["brand_key"] == "amazon_bedrock"
    )
    runtime = next(
        item for item in repository.runtimes if item["provider_id"] == provider["id"]
    )

    for index in range(101):
        request = bedrock_publication(f"failed-model-{index}")
        request.provider = ProviderTarget(existing_id=provider["id"])
        request.runtime = RuntimeTarget(existing_id=runtime["id"])
        failed = service.publish(request, "owner@example.com")
        assert repository.transition_gateway_publication(
            failed.id,
            "queued",
            "failed",
            {"error_code": "test", "error_message": "test"},
            "worker",
        )

    request = bedrock_publication("next-model")
    request.provider = ProviderTarget(existing_id=provider["id"])
    request.runtime = RuntimeTarget(existing_id=runtime["id"])
    publication = service.publish(request, "owner@example.com")

    aliases = {binding.model.model_key for binding in publication.desired_spec.bindings}
    assert "claude-sonnet-4-6-bedrock" in aliases
    assert "next-model" in aliases

def test_credential_rotation_switches_runtime_only_after_promotion() -> None:
    repository = InMemoryRepository()
    cipher = CredentialCipher(Fernet.generate_key())
    service = GatewayControlPlaneService(repository, cipher)
    first = service.publish(bedrock_publication(), "owner@example.com")
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
    runtime = next(
        item
        for item in repository.runtimes
        if item["name"] == "Amazon Bedrock Claude (ap-southeast-2) via APIM"
    )
    original_named_value = runtime["config"]["named_value_name"]
    model_count = len(repository.models)

    rotation = service.rotate_credential(
        APIM_ID,
        GatewayCredentialRotation(
            model_key="claude-sonnet-4-6-bedrock",
            api_key=SecretStr("replacement-key"),
        ),
        "owner@example.com",
    )
    rotated_named_value = rotation.desired_spec.bindings[-1].named_value_name
    foundry_discovery = next(
        item
        for item in rotation.desired_spec.discovery_models
        if item.id == "gpt-5.6-luna"
    )
    client = FakeApimClient()
    worker = GatewayPublicationWorker(
        repository,
        client,
        client.policy,
        cipher=cipher,
    )

    assert rotation.publication_kind.value == "credential_rotation"
    assert foundry_discovery.api_format.value == "openai_chat"
    assert rotated_named_value != original_named_value
    for _ in range(5):
        assert worker.run_once("worker") is not None
    assert runtime["config"]["named_value_name"] == original_named_value
    assert client.current == "1"

    active = worker.run_once("worker")

    assert active is not None and active.status.value == "active"
    assert runtime["config"]["named_value_name"] == rotated_named_value
    assert len(repository.models) == model_count
    assert client.named_values[-1].value == "replacement-key"
