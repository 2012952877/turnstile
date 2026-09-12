from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from tests.backend.model_platform.control_plane_support import (
    TEST_CIPHER,
    FakeApimClient,
    activate_publication,
    external_tenant_foundry_publication,
    foundry_publication,
    publisher_settings,
)
from tests.backend.model_platform.test_image_options import _limits
from turnstile_core.domain.control_plane import (
    ApiFormat,
    GatewayCredentialRotation,
    GatewayPublicationCreate,
    GatewayPublicationRetry,
    GatewayPublicationView,
    PublicationStatus,
    RuntimeTarget,
    StreamingMode,
    publication_model_id,
)
from turnstile_core.domain.runtime_models import ProviderTarget
from turnstile_core.integrations.apim_control_plane_contract import (
    PolicyCompilationError,
    RetryablePublicationError,
)
from turnstile_core.integrations.apim_publisher_client import AzureApimPublisherClient
from turnstile_core.persistence.in_memory import InMemoryRepository
from turnstile_core.services.control_plane import (
    ControlPlaneConflictError,
    ControlPlaneUnavailableError,
    GatewayControlPlaneService,
)
from turnstile_core.services.gateway_publication_worker import GatewayPublicationWorker
from turnstile_core.services.probe_journal import PersistentProbeJournal
from turnstile_core.services.worker_lease import WorkerLease


def image_publication(repository: InMemoryRepository) -> GatewayPublicationCreate:
    request = foundry_publication("image-deployment")
    request.provider = ProviderTarget(
        existing_id=next(
            row["id"] for row in repository.providers if row["brand_key"] == "microsoft_foundry"
        )
    )
    request.model.operation = "image_generation"
    request.model.image_configuration = _limits()
    request.model.input_cost_per_million = 5
    request.model.cached_cost_per_million = 1.25
    request.model.output_cost_per_million = 30
    return request


def test_image_publication_uses_explicit_profile_and_isolated_protocol() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(
        repository,
        apim_principal_id="unit-principal",
        image_generation_enabled=True,
    )
    result = service.publish(image_publication(repository), "owner@example.com")
    binding = result.desired_spec.bindings[-1]
    assert binding.api_format is ApiFormat.OPENAI_IMAGES
    assert binding.streaming_mode is StreamingMode.BUFFERED
    assert binding.backend_path == "/openai/v1/images/generations"
    assert str(binding.backend_url).rstrip("/").endswith(".openai.azure.com")
    assert binding.model.capabilities == ["image_generation"]
    assert binding.model.image_profile is not None
    assert binding.model.image_profile.version == 4
    assert binding.runtime_config["project_endpoint"]
    assert repository.usage_records == []


def test_disabled_images_cannot_queue_publications() -> None:
    repository = InMemoryRepository()
    with pytest.raises(ControlPlaneUnavailableError, match="not enabled"):
        GatewayControlPlaneService(repository).publish(
            image_publication(repository), "owner@example.com"
        )
    assert repository.gateway_publications == []


def test_image_activation_keeps_chat_connection_and_uses_effective_snapshot() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(
        repository,
        apim_principal_id="unit-principal",
        image_generation_enabled=True,
    )
    publication = service.publish(image_publication(repository), "owner@example.com")
    row = next(item for item in repository.gateway_publications if item["id"] == publication.id)
    row["status"] = "promoting"
    repository.claim_gateway_publication("unit-worker", 180)
    repository.activate_gateway_publication(publication.id, "unit-worker")
    binding = publication.desired_spec.bindings[-1]
    model_id = publication_model_id(publication.id, binding.model.model_key)
    route = repository.invocation_route(None, model_id)
    assert route is not None
    assert route["model_capabilities"] == ["image_generation"]
    assert binding.model.image_profile is not None
    assert route["image_profile"] == binding.model.image_profile.model_dump(mode="json")
    assert route["runtime_config"]["api_format"] == "openai_chat"
    assert route["runtime_config"]["path"] == "/chat/completions"
    assert route["runtime_config"]["streaming_mode"] == "native"
    registry_model = next(
        item for item in repository.registry()["models"] if item["id"] == model_id
    )
    assert registry_model["image_profile"] == route["image_profile"]


@pytest.mark.parametrize(
    "field",
    [
        "input_cost_per_million",
        "cached_cost_per_million",
        "output_cost_per_million",
    ],
)
def test_image_prices_are_explicit(field: str) -> None:
    repository = InMemoryRepository()
    request = image_publication(repository)
    setattr(request.model, field, None)
    service = GatewayControlPlaneService(
        repository,
        apim_principal_id="unit-principal",
        image_generation_enabled=True,
    )
    with pytest.raises(ControlPlaneConflictError, match="explicit"):
        service.publish(request, "owner@example.com")


def test_probe_journal_reuses_only_same_verified_plan() -> None:
    repository = InMemoryRepository()
    publication = GatewayControlPlaneService(
        repository,
        image_generation_enabled=True,
        apim_principal_id="unit-principal",
    ).publish(image_publication(repository), "owner@example.com")
    binding = publication.desired_spec.bindings[-1]
    planned = publication_model_id(publication.id, binding.model.model_key)
    authorization = uuid4()
    journal = PersistentProbeJournal(
        repository,
        "unit-publication",
        authorization,
        planned_model_ids={binding.model.model_key: planned},
    )
    requests = []

    def send(request_id: str, model_id: str) -> dict[str, object]:
        requests.append((request_id, model_id))
        return {"total_tokens": 30, "correlation_id": "unit-gateway", "image_validated": True}

    journal.run(binding, "revision", {"prompt": "unit-only"}, send)
    journal.run(binding, "revision", {"prompt": "unit-only"}, send)
    assert len(requests) == 1 and requests[0][1] == str(planned)
    with pytest.raises(PolicyCompilationError):
        journal.run(binding, "changed-revision", {"prompt": "unit-only"}, send)
    assert len(requests) == 1


@pytest.mark.parametrize("status,limit,available", [
    ("queued", 1, False), ("failed", 1, True), ("failed", 31, True),
    ("failed", 32, False), ("failed", 0, False), ("failed", True, False),
])
def test_image_retry_capability_is_failed_only_and_bounded(
    status: str, limit: int, available: bool
) -> None:
    repository = InMemoryRepository()
    publication = GatewayControlPlaneService(
        repository, image_generation_enabled=True, apim_principal_id="unit-principal"
    ).publish(image_publication(repository), "owner@example.com")
    publication = publication.model_copy(update={
        "status": PublicationStatus(status),
        "resource_manifest": {
            "image_probe_authorization": {"id": str(uuid4()), "attempt_limit": limit}
        },
    })
    view = GatewayPublicationView.from_publication(publication)
    assert view.retry_can_authorize_image_probes is available
    assert "resource_manifest" not in view.model_dump()
    assert "desired_spec" not in view.model_dump()


def test_image_retry_authorization_is_explicit_and_cannot_exceed_limit() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(
        repository, image_generation_enabled=True, apim_principal_id="unit-principal"
    )
    publication = service.publish(image_publication(repository), "owner@example.com")
    stored = next(row for row in repository.gateway_publications if row["id"] == publication.id)
    previous = {"id": str(uuid4()), "attempt_limit": 31}
    stored.update(status="failed", resource_manifest={"image_probe_authorization": previous})
    retained = service.retry(publication.id, GatewayPublicationRetry(), "owner@example.com")
    assert retained.resource_manifest["image_probe_authorization"] == previous
    stored["status"] = "failed"
    authorized = service.retry(
        publication.id, GatewayPublicationRetry(authorize_image_probes=True), "owner@example.com"
    )
    current = authorized.resource_manifest["image_probe_authorization"]
    assert isinstance(current, dict)
    assert current["id"] != previous["id"] and current["attempt_limit"] == 32
    stored["status"] = "failed"
    with pytest.raises(ControlPlaneConflictError, match="attempt limit"):
        service.retry(
            publication.id,
            GatewayPublicationRetry(authorize_image_probes=True),
            "owner@example.com",
        )
    assert stored["resource_manifest"]["image_probe_authorization"] == current


@pytest.mark.parametrize("value", [1, "true", None])
def test_paid_image_retry_consent_requires_a_boolean(value: object) -> None:
    with pytest.raises(ValueError):
        GatewayPublicationRetry.model_validate({"authorize_image_probes": value})


def test_lost_lease_prevents_future_work() -> None:
    lease = WorkerLease(lambda: False, 180)
    with pytest.raises(RetryablePublicationError):
        lease.heartbeat()
    with pytest.raises(RetryablePublicationError):
        lease.heartbeat()


def test_expired_publication_lease_cannot_commit_or_activate() -> None:
    repository = InMemoryRepository()
    publication = GatewayControlPlaneService(
        repository,
        image_generation_enabled=True,
        apim_principal_id="unit-principal",
    ).publish(image_publication(repository), "owner@example.com")
    row = repository.claim_gateway_publication("expired-worker", 180)
    assert row is not None
    row["status"] = "promoting"
    repository.gateway_publication_outbox[0]["lease_expires_at"] = datetime.now(UTC) - timedelta(
        seconds=1
    )
    assert (
        repository.transition_gateway_publication(
            publication.id, "promoting", "failed", {}, "expired-worker"
        )
        is None
    )
    with pytest.raises(ValueError, match="current worker lease"):
        repository.activate_gateway_publication(publication.id, "expired-worker")
    assert not repository.billable_requests


def test_removed_image_probe_uses_image_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(400, json={"error": "Model is not published"})

    client = AzureApimPublisherClient(
        publisher_settings(),
        client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    client._probe_removed_model(
        "https://gateway.example.test", {}, "removed-image", ApiFormat.OPENAI_IMAGES
    )
    assert len(requests) == 1
    assert requests[0].url.path == "/images/generations"
    assert b'"prompt"' in requests[0].content and b'"messages"' not in requests[0].content


def test_image_worker_checks_parent_and_journals_before_activation() -> None:
    from tests.backend.model_platform.test_image_policy import image_parent

    repository = InMemoryRepository()
    publication = GatewayControlPlaneService(
        repository,
        image_generation_enabled=True,
        apim_principal_id="unit-principal",
    ).publish(image_publication(repository), "owner@example.com")
    client = FakeApimClient()
    _source, client.policy = image_parent()
    worker = GatewayPublicationWorker(repository, client, client.policy)
    for _step in range(6):
        worker.run_once("unit-worker")
    row = repository.get_gateway_publication(publication.id)
    assert row is not None and row["status"] == "active"
    assert row["resource_manifest"]["images_generations_operation"] is True
    assert len(repository.billable_requests) == 1
    attempt = next(iter(repository.billable_requests.values()))
    assert attempt.state == "exact" and attempt.actual_tokens == 30
    assert attempt.model_id == str(
        publication_model_id(publication.id, publication.desired_spec.bindings[-1].model.model_key)
    )


def test_rotation_uses_shared_connection_id_for_chat_and_image() -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(repository, TEST_CIPHER, image_generation_enabled=True)
    text = service.publish(
        external_tenant_foundry_publication(
            repository, account="unit-account", project="unit", deployment="unit-text"
        ),
        "owner@example.com",
    )
    text_row = repository.get_gateway_publication(text.id)
    assert text_row is not None
    text_row["status"] = "promoting"
    activate_publication(repository, text.id, "unit-worker")
    text_model = next(item for item in repository.models if item["publication_id"] == text.id)
    runtime = next(item for item in repository.runtimes if item["id"] == text_model["runtime_id"])
    old_name = runtime["config"]["named_value_name"]
    image = image_publication(repository)
    image.runtime = RuntimeTarget(existing_id=runtime["id"])
    published = service.publish(image, "owner@example.com")
    image_row = repository.get_gateway_publication(published.id)
    assert image_row is not None
    image_row["status"] = "promoting"
    activate_publication(repository, published.id, "unit-worker")
    rotation = service.rotate_credential(
        published.gateway_profile_id,
        GatewayCredentialRotation(
            model_key=published.desired_spec.bindings[-1].model.model_key,
            api_key=SecretStr("unit-next-key"),
        ),
        "owner@example.com",
    )
    names = {binding.named_value_name for binding in rotation.desired_spec.bindings}
    assert len(names) == 1 and old_name not in names
    assert all(binding.runtime_id == runtime["id"] for binding in rotation.desired_spec.bindings)
    assert runtime["config"]["named_value_name"] == old_name
