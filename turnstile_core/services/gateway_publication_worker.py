from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any

import httpx

from ..domain.control_plane import GatewayPublication, PublicationKind
from ..integrations.apim_control_plane import (
    ApimPolicyCompiler,
    ApimPublisherClient,
    AuthorizationRequiredError,
    PolicyCompilationError,
    RetryablePublicationError,
)
from ..persistence.repository import QueryRepository
from ..security import CredentialCipher
from .control_plane import GatewayControlPlaneService


class GatewayPublicationWorker:
    _NEXT_STATUS = {
        "queued": "validating",
        "validating": "provisioning",
        "provisioning": "building_revision",
        "building_revision": "verifying",
        "verifying": "promoting",
    }

    def __init__(
        self,
        repository: QueryRepository,
        client: ApimPublisherClient,
        parent_policy: str,
        compiler: ApimPolicyCompiler | None = None,
        cipher: CredentialCipher | None = None,
        chat_completions_operation_id: str = "chat-completions",
        responses_operation_id: str = "responses",
        responses_compact_operation_id: str = "responses-compact",
        messages_operation_id: str = "anthropic-messages",
        count_tokens_operation_id: str = "anthropic-count-tokens",
        models_operation_id: str = "anthropic-models",
    ) -> None:
        self._repository = repository
        self._client = client
        self._parent_policy = parent_policy
        self._compiler = compiler or ApimPolicyCompiler()
        self._cipher = cipher
        self._chat_completions_operation_id = chat_completions_operation_id
        self._responses_operation_id = responses_operation_id
        self._responses_compact_operation_id = responses_compact_operation_id
        self._messages_operation_id = messages_operation_id
        self._count_tokens_operation_id = count_tokens_operation_id
        self._models_operation_id = models_operation_id

    def run_once(
        self, worker_id: str, lease_seconds: int = 180, max_attempts: int = 30
    ) -> GatewayPublication | None:
        claimed = self._repository.claim_gateway_publication(worker_id, lease_seconds)
        if claimed is None:
            return None
        publication = GatewayPublication.model_validate(claimed)
        if publication.attempt_count > max_attempts:
            promoted_but_not_recorded = (
                publication.status.value == "promoting"
                and publication.apim_revision is not None
                and self._client.current_revision() == publication.apim_revision
            )
            if promoted_but_not_recorded:
                return self._advance(publication, worker_id)
            failed = self._repository.transition_gateway_publication(
                publication.id,
                publication.status.value,
                "failed",
                {
                    "error_code": "max_attempts_exceeded",
                    "error_message": "Gateway publication exceeded its retry limit",
                },
                worker_id,
            )
            return GatewayPublication.model_validate(failed) if failed else publication
        try:
            return self._advance(publication, worker_id)
        except AuthorizationRequiredError:
            waiting = self._repository.transition_gateway_publication(
                publication.id,
                publication.status.value,
                "awaiting_authorization",
                {
                    "error_code": "provider_authorization_required",
                    "error_message": "Provider authorization is required",
                },
                worker_id,
            )
            return GatewayPublication.model_validate(waiting) if waiting else publication
        except (RetryablePublicationError, httpx.TransportError) as error:
            code = type(error).__name__
            message = str(error).replace("\n", " ")[:1000]
            queued = self._repository.transition_gateway_publication(
                publication.id,
                publication.status.value,
                publication.status.value,
                {"error_code": code, "error_message": message},
                worker_id,
            )
            return GatewayPublication.model_validate(queued) if queued else publication
        except Exception as error:
            code = type(error).__name__
            message = str(error).replace("\n", " ")[:1000]
            if (
                publication.status.value == "promoting"
                and publication.apim_revision is not None
                and self._client.current_revision() == publication.apim_revision
            ):
                queued = self._repository.transition_gateway_publication(
                    publication.id,
                    publication.status.value,
                    publication.status.value,
                    {"error_code": code, "error_message": message},
                    worker_id,
                )
                return GatewayPublication.model_validate(queued) if queued else publication
            failed = self._repository.transition_gateway_publication(
                publication.id,
                publication.status.value,
                "failed",
                {"error_code": code, "error_message": message},
                worker_id,
            )
            return GatewayPublication.model_validate(failed) if failed else publication

    def _advance(self, publication: GatewayPublication, worker_id: str) -> GatewayPublication:
        routed_publication = publication.model_copy(
            update={
                "desired_spec": GatewayControlPlaneService._reconciled_release_spec(
                    publication.desired_spec,
                    self._repository.registry(),
                )
            }
        )
        compiled = self._compiler.compile(routed_publication)
        status = publication.status.value
        updates: dict[str, Any] = {}
        if status == "queued":
            self._compiler.patch_parent_policy(self._parent_policy)
            base_revision, live_policy = self._client.current_api_policy()
            self._compiler.patch_parent_policy(live_policy)
            updates["resource_manifest"] = {
                **publication.resource_manifest,
                "base_apim_revision": base_revision,
                "base_policy_sha256": hashlib.sha256(live_policy.encode("utf-8")).hexdigest(),
            }
        elif status == "validating":
            encrypted = self._repository.gateway_publication_credential(publication.id)
            credential = None
            if encrypted is not None:
                if self._cipher is None:
                    raise PolicyCompilationError("Credential decryption is unavailable")
                credential = self._cipher.decrypt(encrypted)
            latest_named_value = (
                publication.desired_spec.bindings[-1].named_value_name
                if publication.publication_kind is not PublicationKind.MODEL_REMOVE
                else None
            )
            for named_value in compiled.named_values:
                materialized = (
                    replace(named_value, value=credential)
                    if credential is not None and named_value.id == latest_named_value
                    else named_value
                )
                self._client.ensure_named_value(materialized)
            if encrypted is not None:
                self._repository.delete_gateway_publication_credential(publication.id)
            for backend in compiled.backends:
                self._client.ensure_backend(backend)
            updates["resource_manifest"] = {
                **publication.resource_manifest,
                "backends": [backend.id for backend in compiled.backends],
                "named_values": [value.id for value in compiled.named_values],
            }
        elif status == "provisioning":
            revision = f"turnstile-{publication.generation}-{publication.id.hex[:8]}"
            self._client.ensure_revision(revision, f"Turnstile publication {publication.id}")
            updates["apim_revision"] = revision
        elif status == "building_revision":
            if not publication.apim_revision:
                raise RuntimeError("Publication is missing its APIM revision")
            self._client.ensure_revision(
                publication.apim_revision, f"Turnstile publication {publication.id}"
            )
            base_revision, live_policy = self._client.current_api_policy()
            manifest = publication.resource_manifest
            live_hash = hashlib.sha256(live_policy.encode("utf-8")).hexdigest()
            if (
                manifest.get("base_apim_revision") != base_revision
                or manifest.get("base_policy_sha256") != live_hash
            ):
                raise PolicyCompilationError(
                    "The current APIM policy changed after publication was queued"
                )
            parent = self._compiler.patch_parent_policy(live_policy)
            self._client.put_api_policy(publication.apim_revision, parent)
            self._client.put_operation_policy(
                publication.apim_revision,
                self._chat_completions_operation_id,
                compiled.chat_completions_policy,
            )
            self._client.put_operation_policy(
                publication.apim_revision,
                self._responses_operation_id,
                compiled.responses_policy,
            )
            self._client.put_operation_policy(
                publication.apim_revision,
                self._responses_compact_operation_id,
                compiled.responses_compact_policy,
            )
            self._client.put_operation_policy(
                publication.apim_revision,
                self._messages_operation_id,
                compiled.messages_policy,
            )
            self._client.put_operation_policy(
                publication.apim_revision,
                self._count_tokens_operation_id,
                compiled.count_tokens_policy,
            )
            self._client.put_operation_policy(
                publication.apim_revision,
                self._models_operation_id,
                compiled.models_policy,
            )
            updates["policy_sha256"] = compiled.policy_sha256
            updates["resource_manifest"] = {
                **publication.resource_manifest,
                "parent_policy_sha256": hashlib.sha256(
                    parent.encode("utf-8")
                ).hexdigest(),
            }
        elif status == "verifying":
            if not publication.apim_revision:
                raise RuntimeError("Publication is missing its APIM revision")
            self._client.probe_revision(publication.apim_revision, routed_publication)
        elif status == "promoting":
            if not publication.apim_revision:
                raise RuntimeError("Publication is missing its APIM revision")
            if self._client.current_revision() != publication.apim_revision:
                self._client.promote_revision(
                    publication.apim_revision, f"turnstile-{publication.id.hex[:12]}"
                )
                if self._client.current_revision() != publication.apim_revision:
                    raise RetryablePublicationError(
                        "APIM did not report the promoted revision as current"
                    )
            active = self._repository.activate_gateway_publication(publication.id, worker_id)
            return GatewayPublication.model_validate(active)
        else:
            return publication

        next_status = self._NEXT_STATUS[status]
        row = self._repository.transition_gateway_publication(
            publication.id, status, next_status, updates, worker_id
        )
        if row is None:
            raise RuntimeError("Publication changed while the worker held its lease")
        return GatewayPublication.model_validate(row)