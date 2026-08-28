from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID

from ..domain.application_access import (
    GatewayApplicationDiscovery,
    GatewayApplicationSubscriptionProvisionSpec,
)
from ..domain.control_plane import GatewayPublication, GatewayReleaseDependencies


class PolicyCompilationError(ValueError):
    pass


class RetryablePublicationError(RuntimeError):
    pass


class AuthorizationRequiredError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackendResource:
    id: str
    title: str
    url: str
    headers: tuple[tuple[str, str], ...] = ()
    circuit_breaker: BackendCircuitBreakerResource | None = None


@dataclass(frozen=True)
class BackendCircuitBreakerResource:
    failure_count: int
    interval_seconds: int
    trip_duration_seconds: int
    accept_retry_after: bool
    status_code_ranges: tuple[tuple[int, int], ...] = (
        (429, 429),
        (408, 408),
        (500, 599),
    )
    error_reasons: tuple[str, ...] = ("BackendConnectionFailure", "Timeout")


@dataclass(frozen=True)
class BackendPoolMemberResource:
    backend_id: str
    priority: int
    weight: int


@dataclass(frozen=True)
class BackendPoolResource(BackendResource):
    members: tuple[BackendPoolMemberResource, ...] = ()


@dataclass(frozen=True)
class NamedValueResource:
    id: str
    key_vault_secret_id: str | None
    owner_publication_id: str | None = None
    value: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class CompiledGatewayRelease:
    chat_completions_policy: str
    responses_policy: str
    responses_compact_policy: str
    messages_policy: str
    count_tokens_policy: str
    models_policy: str
    policy_sha256: str
    backends: tuple[BackendResource, ...]
    named_values: tuple[NamedValueResource, ...]


@dataclass(frozen=True)
class ReleaseGcPlanEvidence:
    current_non_release_references: dict[str, object]
    candidates: tuple[dict[str, object], ...]
    reference_graph_sha256: str


class ApimSubscriptionKeyClient(Protocol):
    def application_subscription_keys(
        self, apim_subscription_id: str
    ) -> tuple[str, str]: ...

    def regenerate_application_subscription_key(
        self,
        apim_subscription_id: str,
        key_kind: Literal["primary", "secondary"],
    ) -> None: ...


class ApimPublisherClient(Protocol):
    def ensure_backend(self, backend: BackendResource) -> None: ...

    def ensure_named_value(self, named_value: NamedValueResource) -> None: ...

    def ensure_revision(self, revision: str, description: str) -> None: ...

    def put_api_policy(self, revision: str, policy: str) -> None: ...

    def put_operation_policy(self, revision: str, operation: str, policy: str) -> None: ...

    def probe_revision(self, revision: str, publication: GatewayPublication) -> None: ...

    def promote_revision(self, revision: str, release_name: str) -> None: ...

    def current_revision(self) -> str | None: ...

    def current_api_policy(self) -> tuple[str, str]: ...

    def inspect_revision_dependencies(
        self, publication: GatewayPublication
    ) -> GatewayReleaseDependencies: ...

    def plan_release_garbage_collection(
        self,
        releases: Sequence[GatewayPublication],
        retained_release_ids: set[str],
    ) -> ReleaseGcPlanEvidence: ...

    def discover_gateway_applications(
        self, gateway_profile_id: UUID
    ) -> GatewayApplicationDiscovery: ...

    def ensure_application_subscription(
        self,
        spec: GatewayApplicationSubscriptionProvisionSpec,
        primary_key: str,
        secondary_key: str,
    ) -> None: ...