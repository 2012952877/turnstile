from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID
from xml.etree import ElementTree

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from backend.http import service_dependencies
from tests.backend.model_platform.control_plane_support import (
    APIM_ID,
    FakeApimClient,
    GatewayControlPlaneService,
    StubTokenProvider,
    publisher_settings,
)
from turnstile_core.config import Settings
from turnstile_core.domain.application_access import (
    GatewayApplicationSubscriptionCreate,
    GatewayApplicationSubscriptionProvisionSpec,
)
from turnstile_core.integrations.apim_control_plane_contract import (
    PolicyCompilationError,
    RetryablePublicationError,
)
from turnstile_core.integrations.apim_publisher_client import AzureApimPublisherClient
from turnstile_core.integrations.ledger import (
    TableStorageLedger,
    application_map_partition_key,
    application_partition_key,
)
from turnstile_core.persistence.in_memory import InMemoryRepository
from turnstile_core.security import CredentialCipher
from turnstile_core.services.application_access import ApplicationAccessService
from turnstile_core.services.application_provisioning_ledger import prepare_application_ledger
from turnstile_core.services.control_plane import (
    ControlPlaneConflictError,
    ControlPlaneUnavailableError,
)
from turnstile_core.services.gateway_release_operation_worker import GatewayReleaseOperationWorker


def _spec(**overrides: object) -> GatewayApplicationSubscriptionProvisionSpec:
    return GatewayApplicationSubscriptionProvisionSpec.model_validate(
        {
            "application_id": UUID(int=10),
            "application_subscription_id": UUID(int=11),
            "apim_subscription_id": "unit-application",
            "slug": "unit-application",
            "display_name": "Unit application",
            "application_type": "service",
            "scope_id": "unit-product",
            **overrides,
        }
    )


def test_historical_application_spec_remains_readable() -> None:
    spec = _spec()
    assert spec.provisioning_version == 1
    assert spec.gateway_profile_id is None
    assert spec.initial_monthly_token_limit is None


@pytest.mark.parametrize(
    "missing",
    (
        "gateway_profile_id",
        "initial_monthly_token_limit",
        "initial_tokens_per_minute",
    ),
)
def test_staged_application_spec_requires_bound_gateway_and_limits(missing: str) -> None:
    values: dict[str, object] = {
        "provisioning_version": 2,
        "gateway_profile_id": UUID(int=1),
        "initial_monthly_token_limit": 1000,
        "initial_tokens_per_minute": 100,
    }
    assert _spec(**values).provisioning_version == 2
    values.pop(missing)
    with pytest.raises(ValueError, match="snapshotted budget limits"):
        _spec(**values)


@pytest.mark.parametrize("value", (0, -1))
def test_staged_application_spec_rejects_nonpositive_limits(value: int) -> None:
    with pytest.raises(ValueError):
        _spec(
            provisioning_version=2,
            gateway_profile_id=UUID(int=1),
            initial_monthly_token_limit=value,
            initial_tokens_per_minute=100,
        )


class AdmissionRows:
    def __init__(self, missing: str | None = None) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.missing = missing

    def upsert(self, partition: str, row_key: str, entity: dict[str, Any]) -> None:
        self.rows[partition, row_key] = dict(entity)

    def insert_if_missing(self, partition: str, row_key: str, entity: dict[str, Any]) -> None:
        self.rows.setdefault((partition, row_key), dict(entity))

    def read_entity(self, partition: str, row_key: str) -> dict[str, Any] | None:
        return None if row_key == self.missing else self.rows.get((partition, row_key))


def _materialized_application() -> tuple[
    InMemoryRepository, UUID, GatewayApplicationSubscriptionProvisionSpec
]:
    repository = InMemoryRepository()
    gateway_id = next(
        item["id"] for item in repository.gateways if item["implementation"] == "apim"
    )
    spec = _spec(
        provisioning_version=2,
        gateway_profile_id=gateway_id,
        initial_monthly_token_limit=1000,
        initial_tokens_per_minute=100,
    )
    ApplicationAccessService(repository, sync_available=True).provision(
        gateway_id, spec, "owner@example.com"
    )
    return repository, gateway_id, spec


def test_application_ledger_prepare_preserves_confirmed_usage_and_reservations() -> None:
    repository, gateway_id, spec = _materialized_application()
    store = AdmissionRows()
    partition = application_partition_key(spec.application_id, datetime.now(UTC).strftime("%Y-%m"))
    store.rows[partition, "C"] = {"ConfirmedUsed": 55}
    store.rows[partition, "R|pending|request"] = {"Reserved": 40}
    store.rows["unrelated", "C"] = {"ConfirmedUsed": 200}
    for _ in range(2):
        prepare_application_ledger(
            repository, cast(TableStorageLedger, store), gateway_id, spec.application_id
        )
    assert store.rows[partition, "C"] == {"ConfirmedUsed": 55}
    assert store.rows[partition, "R|pending|request"] == {"Reserved": 40}
    assert store.rows["unrelated", "C"] == {"ConfirmedUsed": 200}
    assert store.rows[partition, "Q"]["Limit"] == 1000
    assert store.rows[partition, "Q"]["TokensPerMinute"] == 100
    mapping = store.rows[application_map_partition_key(gateway_id), spec.apim_subscription_id]
    assert mapping["ApplicationId"] == str(spec.application_id)
    assert mapping["ScopeExists"] is True
    assert len(store.rows) == 6


@pytest.mark.parametrize("missing", ("Q", "C", "M", "unit-application"))
def test_application_ledger_prepare_requires_complete_readback(missing: str) -> None:
    repository, gateway_id, spec = _materialized_application()
    store = AdmissionRows(missing)
    with pytest.raises(RetryablePublicationError, match="readback is incomplete"):
        prepare_application_ledger(
            repository, cast(TableStorageLedger, store), gateway_id, spec.application_id
        )


def test_application_ledger_prepare_never_crosses_gateway_identity() -> None:
    repository, _gateway_id, spec = _materialized_application()
    store = AdmissionRows()
    with pytest.raises(RetryablePublicationError, match="not ready"):
        prepare_application_ledger(
            repository, cast(TableStorageLedger, store), UUID(int=99), spec.application_id
        )
    assert store.rows == {}


def _admission_policy(gateway_id: UUID) -> str:
    root = ElementTree.Element("policies")
    inbound = ElementTree.SubElement(root, "inbound")
    ElementTree.SubElement(
        inbound,
        "set-variable",
        {
            "name": "applicationMapPartition",
            "value": f'@("app-map|{gateway_id}")',
        },
    )
    ElementTree.SubElement(
        inbound,
        "set-variable",
        {
            "name": "telemetryGatewayProfileId",
            "value": str(gateway_id),
        },
    )
    return ElementTree.tostring(root, encoding="unicode")


def test_application_subscription_is_suspended_until_owned_activation_with_etag() -> None:
    spec = _spec(
        provisioning_version=2,
        gateway_profile_id=UUID(int=1),
        initial_monthly_token_limit=1000,
        initial_tokens_per_minute=100,
    )
    stored: dict[str, Any] = {}
    writes: list[httpx.Request] = []
    membership: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/policies/policy"):
            return httpx.Response(
                200,
                headers={"content-type": "application/xml"},
                text=_admission_policy(UUID(int=1)),
            )
        if "/products/" in request.url.path:
            if "/apis/" in request.url.path:
                membership.append(request.method)
                return httpx.Response(204)
            return httpx.Response(
                200,
                json={
                    "properties": {
                        "state": "published",
                        "subscriptionRequired": True,
                    }
                },
            )
        if request.method == "PUT":
            writes.append(request)
            if stored:
                assert request.headers["If-Match"] == '"unit-etag"'
            stored.update(json.loads(request.content)["properties"])
            return httpx.Response(200, json={"properties": stored})
        return (
            httpx.Response(200, headers={"ETag": '"unit-etag"'}, json={"properties": stored})
            if stored
            else httpx.Response(404)
        )

    client = AzureApimPublisherClient(
        publisher_settings(),
        StubTokenProvider(),  # type: ignore[arg-type]
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client.ensure_application_subscription(spec, "unit-primary", "unit-secondary")
    assert stored["state"] == "suspended"
    assert stored["displayName"].startswith("Turnstile pending ")
    assert "unit-primary" not in stored["displayName"]
    client.ensure_application_subscription(spec, "unit-primary", "unit-secondary")
    assert len(writes) == 1
    client.activate_application_subscription(spec, "unit-primary", "unit-secondary")
    assert stored["state"] == "active"
    assert stored["displayName"] == spec.display_name
    assert stored["allowTracing"] is False
    client.activate_application_subscription(spec, "unit-primary", "unit-secondary")
    assert len(writes) == 2
    assert membership == ["HEAD", "HEAD"]


@pytest.mark.parametrize("rejection", ("gateway", "product", "membership"))
def test_application_preflight_rejection_never_writes_a_subscription(rejection: str) -> None:
    spec = _spec(
        provisioning_version=2,
        gateway_profile_id=UUID(int=1),
        initial_monthly_token_limit=1000,
        initial_tokens_per_minute=100,
    )
    writes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            writes.append(request.url.path)
        if request.url.path.endswith("/policies/policy"):
            gateway = UUID(int=2) if rejection == "gateway" else UUID(int=1)
            return httpx.Response(
                200, headers={"content-type": "application/xml"}, text=_admission_policy(gateway)
            )
        if request.method == "HEAD":
            return httpx.Response(404 if rejection == "membership" else 204)
        return httpx.Response(
            200,
            json={
                "properties": {
                    "state": "published",
                    "subscriptionRequired": rejection != "product",
                }
            },
        )

    client = AzureApimPublisherClient(
        publisher_settings(),
        StubTokenProvider(),  # type: ignore[arg-type]
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(PolicyCompilationError):
        client.ensure_application_subscription(spec, "unit-primary", "unit-secondary")
    assert writes == []


@pytest.mark.parametrize(
    "missing",
    (
        "gateway_release_worker_enabled",
        "gateway_application_provisioning_enabled",
        "ledger_table_endpoint",
        "credential_encryption_key",
        "azure_subscription_id",
        "apim_resource_group",
        "apim_service_name",
    ),
)
def test_creation_capability_requires_all_deployed_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    settings = Settings(
        control_plane_enabled=True,
        gateway_release_worker_enabled=True,
        gateway_application_provisioning_enabled=True,
        ledger_table_endpoint="https://ledger.example.test",
        credential_encryption_key=SecretStr(Fernet.generate_key().decode()),
        azure_subscription_id=str(UUID(int=5)),
        apim_resource_group="unit-rg",
        apim_service_name="unit-apim",
        apim_usage_observer_url="https://observer.example.test",
        apim_usage_observer_key_named_value="unit-observer-key",
    )
    assert service_dependencies._application_provisioning_available(settings)
    settings = settings.model_copy(
        update={
            missing: False if missing.endswith("enabled") else None,
        }
    )
    monkeypatch.setattr(service_dependencies, "get_settings", lambda: settings)
    repository = InMemoryRepository()
    inventory = service_dependencies.application_access_service(repository).applications()
    assert inventory.provisioning_available is False
    assert inventory.provisioning_unavailable_reason
    assert inventory.provisioning_defaults is not None
    service = service_dependencies.control_plane_service(repository)
    with pytest.raises(ControlPlaneUnavailableError):
        service.request_application_subscription_provision(
            UUID(int=1),
            GatewayApplicationSubscriptionCreate(
                subscription_id="unavailable",
                display_name="Unavailable",
            ),
            "owner@example.com",
        )
    assert repository.gateway_release_operations == []


class StagedProvisioner(FakeApimClient):
    def __init__(self) -> None:
        super().__init__()
        self.subscription: tuple[GatewayApplicationSubscriptionProvisionSpec, str, str] | None = (
            None
        )
        self.suspend_count = 0
        self.active = False

    def ensure_application_subscription(
        self,
        spec: GatewayApplicationSubscriptionProvisionSpec,
        primary_key: str,
        secondary_key: str,
    ) -> None:
        self.subscription = (spec, primary_key, secondary_key)
        self.suspend_count += 1

    def activate_application_subscription(
        self,
        spec: GatewayApplicationSubscriptionProvisionSpec,
        primary_key: str,
        secondary_key: str,
    ) -> None:
        assert self.subscription == (spec, primary_key, secondary_key)
        self.active = True


def _queued_creation() -> tuple[InMemoryRepository, CredentialCipher, UUID]:
    repository = InMemoryRepository()
    cipher = CredentialCipher(Fernet.generate_key())
    service = GatewayControlPlaneService(
        repository,
        cipher,
        application_default_token_limit=24_000,
        application_default_tokens_per_minute=1500,
        application_product_id="unit-product",
    )
    accepted = service.request_application_subscription_provision(
        APIM_ID,
        GatewayApplicationSubscriptionCreate(
            subscription_id="staged-unit-app",
            display_name="Staged unit app",
        ),
        "owner@example.com",
    )
    return repository, cipher, accepted.operation.id


def test_ledger_retry_cannot_activate_or_change_frozen_budgets_and_keys() -> None:
    repository, cipher, operation_id = _queued_creation()
    client = StagedProvisioner()
    ciphertext = repository.gateway_release_operation_secret(operation_id)
    attempts: list[UUID] = []
    store = AdmissionRows()

    def project(gateway_id: UUID, application_id: UUID) -> None:
        assert gateway_id == APIM_ID
        attempts.append(application_id)
        if len(attempts) == 1:
            raise RuntimeError("unit ledger propagation delay")
        prepare_application_ledger(
            repository, cast(TableStorageLedger, store), gateway_id, application_id
        )

    worker = GatewayReleaseOperationWorker(
        repository,
        client,
        cipher=cipher,
        application_projector=project,
        application_default_token_limit=1,
        application_default_tokens_per_minute=1,
    )
    for expected in (
        "validating_dependencies",
        "promoting",
        "verifying_readback",
        "verifying_readback",
    ):
        result = worker.run_once("unit-worker")
        assert result is not None and result.status == expected
        assert result.id == operation_id
        assert not client.active
        assert repository.gateway_release_operation_secret(operation_id) == ciphertext
    assert client.subscription is not None
    assert client.subscription[0].scope_id == "unit-product"
    assert client.subscription[0].initial_monthly_token_limit == 24_000
    completed = worker.run_once("unit-worker")
    assert completed is not None and completed.status == "succeeded"
    assert completed.error_message is None
    assert completed.checkpoint["admission_ready"] is True
    assert client.active and client.suspend_count == 1
    assert repository.gateway_release_operation_secret(operation_id) is None
    assert len(repository.gateway_applications) == len(repository.gateway_release_operations) == 1
    assert (
        next(value for (partition, key), value in store.rows.items() if key == "Q")["Limit"]
        == 24_000
    )


@pytest.mark.parametrize("historical", (False, True))
def test_missing_projector_fails_new_creation_before_write_but_preserves_historical_path(
    historical: bool,
) -> None:
    repository, cipher, operation_id = _queued_creation()
    if historical:
        preview = repository.gateway_release_operations[0]["semantic_preview"]
        for field in (
            "provisioning_version",
            "gateway_profile_id",
            "initial_monthly_token_limit",
            "initial_tokens_per_minute",
        ):
            preview.pop(field)
    client = StagedProvisioner()
    worker = GatewayReleaseOperationWorker(repository, client, cipher=cipher)
    worker.run_once("unit-worker")
    result = worker.run_once("unit-worker")
    if historical:
        assert result is not None and result.status == "promoting"
        result = worker.run_once("unit-worker")
        assert result is not None and result.status == "succeeded"
        assert client.suspend_count == 1
    else:
        assert result is not None and result.status == "failed"
        assert client.suspend_count == 0
        assert repository.gateway_applications == []
    assert repository.gateway_release_operation_secret(operation_id) is None


def test_terminal_ledger_failure_keeps_subscription_suspended_and_clears_secret() -> None:
    repository, cipher, operation_id = _queued_creation()
    client = StagedProvisioner()

    def reject(_gateway: UUID, _application: UUID) -> None:
        raise PolicyCompilationError("unit scope no longer exists")

    worker = GatewayReleaseOperationWorker(
        repository, client, cipher=cipher, application_projector=reject
    )
    for _ in range(3):
        worker.run_once("unit-worker")
    failed = worker.run_once("unit-worker")
    assert failed is not None and failed.status == "failed"
    assert not client.active
    assert client.suspend_count == 1
    assert failed.checkpoint["apim_subscription_created"] is True
    assert repository.gateway_release_operation_secret(operation_id) is None


@pytest.mark.parametrize(
    "subscription_id", ("master", "turnstile-dashboard", "turnstile-publisher-probe")
)
def test_system_subscription_ids_cannot_be_queued(subscription_id: str) -> None:
    repository = InMemoryRepository()
    service = GatewayControlPlaneService(repository, CredentialCipher(Fernet.generate_key()))
    with pytest.raises(ControlPlaneConflictError, match="reserved"):
        service.request_application_subscription_provision(
            APIM_ID,
            GatewayApplicationSubscriptionCreate(
                subscription_id=subscription_id,
                display_name="Reserved",
            ),
            "owner@example.com",
        )
    assert repository.gateway_release_operations == []


def test_foreign_gateway_in_staged_operation_is_rejected_before_any_side_effect() -> None:
    repository, cipher, operation_id = _queued_creation()
    repository.gateway_release_operations[0]["semantic_preview"]["gateway_profile_id"] = str(
        UUID(int=99)
    )
    client = StagedProvisioner()
    worker = GatewayReleaseOperationWorker(repository, client, cipher=cipher)
    failed = worker.run_once("unit-worker")
    assert failed is not None and failed.status == "failed"
    assert client.subscription is None
    assert repository.gateway_release_operation_secret(operation_id) is None
