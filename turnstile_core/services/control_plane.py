from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

from ..domain.application_access import (
    GatewayApplicationSubscriptionCreate,
    GatewayApplicationSubscriptionProvisionSpec,
    application_id_for,
    application_subscription_id_for,
)
from ..domain.control_plane import (
    ApiFormat,
    AuthStrategy,
    DiscoveryModel,
    GatewayApplicationSubscriptionProvisionAccepted,
    GatewayBackendPoolConfig,
    GatewayBackendPoolMember,
    GatewayBackendPoolMemberWrite,
    GatewayBackendPoolWrite,
    GatewayCredentialRotation,
    GatewayModelBinding,
    GatewayPublication,
    GatewayPublicationCreate,
    GatewayPublicationRetry,
    GatewayPublicationView,
    GatewayReleaseAuditEvent,
    GatewayReleaseChangeSet,
    GatewayReleaseDependencies,
    GatewayReleaseDetail,
    GatewayReleaseDiff,
    GatewayReleaseIntegrity,
    GatewayReleaseList,
    GatewayReleaseOperation,
    GatewayReleaseOperationAccepted,
    GatewayReleaseOperationAuditEvent,
    GatewayReleaseOperationKind,
    GatewayReleaseProtection,
    GatewayReleaseProtectionAuditEvent,
    GatewayReleaseProtectionWrite,
    GatewayReleaseRetentionPolicy,
    GatewayReleaseRole,
    GatewayReleaseRollbackPreview,
    GatewayReleaseRollbackRequest,
    GatewayReleaseSpec,
    GatewayReleaseSummary,
    ModelCreateTarget,
    ModelRemovalTarget,
    ModelTarget,
    PublicationKind,
    PublicationStatus,
    StreamingMode,
    publication_materialized_named_values,
    publication_retry_requires_credential,
)
from ..domain.runtime_models import (
    FOUNDRY_INFERENCE_RESOURCE,
    FOUNDRY_INFERENCE_ROLE_ID,
    BrandKey,
    ModelCapability,
    ModelFamilyKey,
    foundry_runtime_name,
    openai_compatible_endpoint_values,
    openai_compatible_runtime_name,
)
from ..persistence.repository import QueryRepository
from ..security import CredentialCipher


class ControlPlaneConflictError(ValueError):
    pass


class ControlPlaneNotFoundError(ValueError):
    pass


class ControlPlaneUnavailableError(RuntimeError):
    pass


_RELEASE_WORKER_UNAVAILABLE = (
    "Gateway Release operation worker is not enabled in this environment"
)
_APPLICATION_PROVISIONING_UNAVAILABLE = (
    "Application subscription provisioning is not enabled in this environment"
)
_APPLICATION_PRODUCT_ID = "finops-ai-consumers"


class GatewayControlPlaneService:
    def __init__(
        self,
        repository: QueryRepository,
        cipher: CredentialCipher | None = None,
        apim_principal_id: str | None = None,
        retention_policy: GatewayReleaseRetentionPolicy | None = None,
        release_worker_enabled: bool = True,
        application_provisioning_enabled: bool = True,
        application_default_token_limit: int = 100_000,
        application_default_tokens_per_minute: int = 100_000,
        application_product_id: str = _APPLICATION_PRODUCT_ID,
    ) -> None:
        self._repository = repository
        self._cipher = cipher
        self._apim_principal_id = apim_principal_id
        self._release_worker_enabled = release_worker_enabled
        self._application_provisioning_enabled = application_provisioning_enabled
        self._application_default_token_limit = application_default_token_limit
        self._application_default_tokens_per_minute = application_default_tokens_per_minute
        self._application_product_id = application_product_id
        self._retention_policy = retention_policy or GatewayReleaseRetentionPolicy(
            retained_count=20,
            retained_days=180,
            failed_retained_days=30,
            protected_labels=["milestone", "rollback"],
        )

    def publications(
        self, gateway_profile_id: UUID | None = None, limit: int = 50
    ) -> list[GatewayPublication]:
        return [
            GatewayPublication.model_validate(row)
            for row in self._repository.list_gateway_publications(gateway_profile_id, limit)
        ]

    def publication(self, publication_id: UUID) -> GatewayPublication:
        row = self._repository.get_gateway_publication(publication_id)
        if row is None:
            raise ControlPlaneNotFoundError("Gateway publication not found")
        publication = GatewayPublication.model_validate(row)
        if not publication_retry_requires_credential(publication):
            return publication
        audit = self._repository.list_gateway_publication_audit(publication_id)
        materialized = publication_materialized_named_values(
            publication,
            provisioning_completed=any(
                item.get("from_status") == "validating"
                and item.get("to_status") == "provisioning"
                for item in audit
            ),
        )
        if not materialized:
            return publication
        return publication.model_copy(
            update={
                "resource_manifest": {
                    **publication.resource_manifest,
                    "named_values": materialized,
                }
            }
        )

    def releases(
        self, gateway_profile_id: UUID | None = None, limit: int = 100
    ) -> GatewayReleaseList:
        publications = self.publications(gateway_profile_id, limit)
        base_ids = {
            publication.base_release_id
            for publication in publications
            if publication.base_release_id is not None
        }
        related = {
            publication.id: publication
            for publication in publications
        }
        related.update(
            {
                publication.id: publication
                for row in self._repository.get_gateway_publications(list(base_ids))
                if (publication := GatewayPublication.model_validate(row))
            }
        )
        integrity = {
            UUID(str(row["publication_id"])): row
            for row in self._repository.latest_gateway_release_integrity_snapshots(
                [publication.id for publication in publications]
            )
        }
        contexts = self._release_contexts(publications)
        retention = self._release_retention(publications, contexts)
        return GatewayReleaseList(
            items=[
                self._release_summary(
                    publication,
                    contexts[publication.gateway_profile_id],
                    retention[publication.id],
                    (
                        related.get(publication.base_release_id)
                        if publication.base_release_id is not None
                        else None
                    ),
                    self._release_dependencies_from_snapshot(
                        publication, integrity.get(publication.id)
                    ),
                )
                for publication in publications
            ],
            retention_policy=self._retention_policy,
            operations_enabled=self._release_worker_enabled,
            operations_disabled_reason=(
                None if self._release_worker_enabled else _RELEASE_WORKER_UNAVAILABLE
            ),
        )

    def release(self, release_id: UUID) -> GatewayReleaseDetail:
        publication = self.publication(release_id)
        related = self.publications(publication.gateway_profile_id, 1000)
        context = self._release_contexts(related)[publication.gateway_profile_id]
        retention = self._release_retention(
            related, {publication.gateway_profile_id: context}
        )
        against = self._optional_publication(publication.base_release_id)
        dependencies = self._release_dependencies(publication)
        summary = self._release_summary(
            publication,
            context,
            retention[publication.id],
            against,
            dependencies,
        )
        audit = [
            GatewayReleaseAuditEvent.model_validate(row)
            for row in self._repository.list_gateway_publication_audit(publication.id)
        ]
        protection_audit = [
            GatewayReleaseProtectionAuditEvent.model_validate(row)
            for row in self._repository.list_gateway_release_protection_audit(
                publication.id
            )
        ]
        return GatewayReleaseDetail(
            **summary.model_dump(),
            base_release_id=publication.base_release_id,
            diff_from_base=GatewayReleaseDiff(
                release_id=publication.id,
                against_release_id=against.id if against else None,
                changes=self._release_changes(publication, against),
            ),
            dependencies=dependencies,
            audit=audit,
            protection_audit=protection_audit,
        )

    def release_diff(
        self, release_id: UUID, against_release_id: UUID | None = None
    ) -> GatewayReleaseDiff:
        release = self.publication(release_id)
        against = self._optional_publication(
            against_release_id if against_release_id is not None else release.base_release_id
        )
        if against is not None and against.gateway_profile_id != release.gateway_profile_id:
            raise ControlPlaneConflictError("Gateway releases belong to different gateways")
        return GatewayReleaseDiff(
            release_id=release.id,
            against_release_id=against.id if against else None,
            changes=self._release_changes(release, against),
        )

    def release_integrity(self, release_id: UUID) -> GatewayReleaseIntegrity:
        publication = self.publication(release_id)
        related = self.publications(publication.gateway_profile_id, 1000)
        context = self._release_contexts(related)[publication.gateway_profile_id]
        dependencies = self._release_dependencies(publication)
        blockers = self._rollback_blockers(
            publication,
            dependencies,
            is_current=publication.id == context["current_id"],
        )
        return GatewayReleaseIntegrity(
            release_id=publication.id,
            dependencies=dependencies,
            rollback_eligible=not blockers,
            rollback_blockers=blockers,
        )

    def protect_release(
        self,
        release_id: UUID,
        write: GatewayReleaseProtectionWrite,
        actor: str,
    ) -> GatewayReleaseProtection | None:
        self.publication(release_id)
        if (
            write.protected_label is not None
            and write.protected_label not in self._retention_policy.protected_labels
        ):
            raise ControlPlaneConflictError("Unsupported release protection label")
        row = self._repository.set_gateway_release_protection(
            release_id,
            pinned=write.pinned,
            protected_label=write.protected_label,
            retain_until=write.retain_until,
            updated_by=actor,
        )
        return GatewayReleaseProtection.model_validate(row) if row is not None else None

    def rollback_preview(self, release_id: UUID) -> GatewayReleaseRollbackPreview:
        target = self.publication(release_id)
        current_row = self._repository.effective_gateway_publication(
            target.gateway_profile_id
        )
        if current_row is None:
            raise ControlPlaneConflictError("The gateway has no effective release")
        current = GatewayPublication.model_validate(current_row)
        dependencies = self._release_dependencies(target)
        blockers = self._rollback_blockers(
            target, dependencies, is_current=target.id == current.id
        )
        changes = self._release_changes(target, current)
        payload = {
            "target_release_id": str(target.id),
            "target_desired_spec_sha256": target.desired_spec_sha256,
            "target_policy_sha256": target.policy_sha256,
            "current_release_id": str(current.id),
            "current_desired_spec_sha256": current.desired_spec_sha256,
            "current_policy_sha256": current.policy_sha256,
            "changes": changes.model_dump(mode="json"),
            "dependencies": dependencies.model_dump(
                mode="json", exclude={"live_checked_at"}
            ),
        }
        confirmation_sha256 = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return GatewayReleaseRollbackPreview(
            target_release_id=target.id,
            current_release_id=current.id,
            changes=changes,
            dependencies=dependencies,
            rollback_eligible=not blockers,
            rollback_blockers=blockers,
            confirmation_sha256=confirmation_sha256,
        )

    def request_rollback(
        self,
        release_id: UUID,
        request: GatewayReleaseRollbackRequest,
        actor: str,
    ) -> GatewayReleaseOperationAccepted:
        self._require_release_worker()
        preview = self.rollback_preview(release_id)
        if not preview.rollback_eligible:
            raise ControlPlaneConflictError(
                f"Release is not rollback eligible: {', '.join(preview.rollback_blockers)}"
            )
        if not hmac.compare_digest(
            preview.confirmation_sha256, request.confirmation_sha256
        ):
            raise ControlPlaneConflictError("Rollback preview changed; confirm it again")
        target = self.publication(release_id)
        try:
            row = self._repository.create_gateway_release_operation(
                target.gateway_profile_id,
                GatewayReleaseOperationKind.ROLLBACK.value,
                actor,
                target_release_id=target.id,
                prior_release_id=preview.current_release_id,
                confirmation_sha256=preview.confirmation_sha256,
                semantic_preview=preview.model_dump(mode="json"),
            )
        except ValueError as error:
            raise ControlPlaneConflictError(str(error)) from error
        operation = self._operation_view(row)
        return GatewayReleaseOperationAccepted(
            operation=operation,
            status_url=f"/api/v1/model-management/release-operations/{operation.id}",
        )

    def request_integrity_check(
        self, release_id: UUID, actor: str
    ) -> GatewayReleaseOperationAccepted:
        self._require_release_worker()
        release = self.publication(release_id)
        try:
            row = self._repository.create_gateway_release_operation(
                release.gateway_profile_id,
                GatewayReleaseOperationKind.INTEGRITY_CHECK.value,
                actor,
                target_release_id=release.id,
            )
        except ValueError as error:
            raise ControlPlaneConflictError(str(error)) from error
        operation = self._operation_view(row)
        return GatewayReleaseOperationAccepted(
            operation=operation,
            status_url=f"/api/v1/model-management/release-operations/{operation.id}",
        )

    def request_gc_plan(
        self, gateway_profile_id: UUID, actor: str
    ) -> GatewayReleaseOperationAccepted:
        self._require_release_worker()
        current = self._repository.effective_gateway_publication(gateway_profile_id)
        if current is None:
            raise ControlPlaneConflictError("The gateway has no effective release")
        try:
            row = self._repository.create_gateway_release_operation(
                gateway_profile_id,
                GatewayReleaseOperationKind.GC_PLAN.value,
                actor,
                prior_release_id=UUID(str(current["id"])),
            )
        except ValueError as error:
            raise ControlPlaneConflictError(str(error)) from error
        operation = self._operation_view(row)
        return GatewayReleaseOperationAccepted(
            operation=operation,
            status_url=f"/api/v1/model-management/release-operations/{operation.id}",
        )

    def request_application_sync(
        self, gateway_profile_id: UUID, actor: str
    ) -> GatewayReleaseOperationAccepted:
        self._require_release_worker()
        gateway = next(
            (
                item
                for item in self._repository.registry()["gateways"]
                if UUID(str(item["id"])) == gateway_profile_id
            ),
            None,
        )
        if gateway is None or gateway.get("implementation") != "apim":
            raise ControlPlaneNotFoundError("APIM gateway not found")
        try:
            row = self._repository.create_gateway_release_operation(
                gateway_profile_id,
                GatewayReleaseOperationKind.APPLICATION_SYNC.value,
                actor,
            )
        except ValueError as error:
            raise ControlPlaneConflictError(str(error)) from error
        operation = self._operation_view(row)
        return GatewayReleaseOperationAccepted(
            operation=operation,
            status_url=f"/api/v1/model-management/release-operations/{operation.id}",
        )

    def request_application_subscription_provision(
        self,
        gateway_profile_id: UUID,
        request: GatewayApplicationSubscriptionCreate,
        actor: str,
    ) -> GatewayApplicationSubscriptionProvisionAccepted:
        self._require_release_worker()
        if not self._application_provisioning_enabled:
            raise ControlPlaneUnavailableError(_APPLICATION_PROVISIONING_UNAVAILABLE)
        if self._cipher is None:
            raise ControlPlaneUnavailableError("Credential encryption is unavailable")
        if request.subscription_id in {
            "master", "turnstile-dashboard", "turnstile-publisher-probe"
        }:
            raise ControlPlaneConflictError("This APIM subscription ID is reserved for the system")
        gateway = next(
            (
                item
                for item in self._repository.registry()["gateways"]
                if UUID(str(item["id"])) == gateway_profile_id
            ),
            None,
        )
        if gateway is None or gateway.get("implementation") != "apim":
            raise ControlPlaneNotFoundError("APIM gateway not found")
        if not gateway.get("enabled"):
            raise ControlPlaneConflictError("APIM gateway is disabled")
        if any(
            str(item["apim_subscription_id"]).casefold()
            == request.subscription_id.casefold()
            for item in self._repository.gateway_application_attribution_map(
                gateway_profile_id
            )
        ):
            raise ControlPlaneConflictError("APIM subscription ID already exists")
        spec = GatewayApplicationSubscriptionProvisionSpec(
            provisioning_version=2,
            gateway_profile_id=gateway_profile_id,
            initial_monthly_token_limit=self._application_default_token_limit,
            initial_tokens_per_minute=self._application_default_tokens_per_minute,
            application_id=application_id_for(
                gateway_profile_id, request.subscription_id
            ),
            application_subscription_id=application_subscription_id_for(
                gateway_profile_id, request.subscription_id
            ),
            apim_subscription_id=request.subscription_id,
            slug=request.subscription_id,
            display_name=request.display_name,
            description=request.description,
            application_type=request.application_type,
            scope_id=self._application_product_id,
        )
        primary_key = secrets.token_urlsafe(32)
        ciphertext = self._cipher.encrypt(
            json.dumps(
                {
                    "primary_key": primary_key,
                    "secondary_key": secrets.token_urlsafe(32),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        try:
            row = self._repository.create_gateway_release_operation(
                gateway_profile_id,
                GatewayReleaseOperationKind.APPLICATION_PROVISION.value,
                actor,
                semantic_preview=spec.model_dump(mode="json"),
                credential_ciphertext=ciphertext,
            )
        except ValueError as error:
            raise ControlPlaneConflictError(str(error)) from error
        operation = self._operation_view(row)
        return GatewayApplicationSubscriptionProvisionAccepted(
            operation=operation,
            status_url=(
                f"/api/v1/model-management/release-operations/{operation.id}"
            ),
            primary_key=primary_key,
        )

    def release_operation(self, operation_id: UUID) -> GatewayReleaseOperation:
        row = self._repository.get_gateway_release_operation(operation_id)
        if row is None:
            raise ControlPlaneNotFoundError("Gateway release operation not found")
        return self._operation_view(row)

    def retained_release_ids(self, gateway_profile_id: UUID) -> set[UUID]:
        publications = [
            GatewayPublication.model_validate(row)
            for row in self._repository.all_gateway_publications(gateway_profile_id)
        ]
        if not publications:
            return set()
        contexts = self._release_contexts(publications)
        retention = self._release_retention(publications, contexts)
        return {
            publication.id
            for publication in publications
            if bool(retention[publication.id]["retention_eligible"])
        }

    def _operation_view(self, row: Mapping[str, Any]) -> GatewayReleaseOperation:
        operation_id = UUID(str(row["id"]))
        audit = [
            GatewayReleaseOperationAuditEvent.model_validate(value)
            for value in self._repository.list_gateway_release_operation_audit(
                operation_id
            )
        ]
        gc_row = self._repository.get_gateway_release_gc_plan(operation_id)
        fields = GatewayReleaseOperation.model_fields
        values = {key: row[key] for key in fields if key in row}
        values["worker_available"] = self._release_worker_enabled
        values["worker_unavailable_reason"] = (
            None if self._release_worker_enabled else _RELEASE_WORKER_UNAVAILABLE
        )
        values["audit"] = audit
        if gc_row is not None:
            values["gc_plan"] = gc_row
        return GatewayReleaseOperation.model_validate(values)

    def _require_release_worker(self) -> None:
        if not self._release_worker_enabled:
            raise ControlPlaneUnavailableError(_RELEASE_WORKER_UNAVAILABLE)

    def _optional_publication(self, publication_id: UUID | None) -> GatewayPublication | None:
        if publication_id is None:
            return None
        row = self._repository.get_gateway_publication(publication_id)
        return GatewayPublication.model_validate(row) if row is not None else None

    def _release_contexts(
        self, publications: Sequence[GatewayPublication]
    ) -> dict[UUID, dict[str, object]]:
        grouped: dict[UUID, list[GatewayPublication]] = {}
        for publication in publications:
            grouped.setdefault(publication.gateway_profile_id, []).append(publication)
        gateway_names = {
            UUID(str(row["id"])): str(row["name"])
            for row in self._repository.get_gateway_profiles(list(grouped))
        }
        contexts: dict[UUID, dict[str, object]] = {}
        for gateway_id, rows in grouped.items():
            effective_row = self._repository.effective_gateway_publication(gateway_id)
            current = (
                GatewayPublication.model_validate(effective_row)
                if effective_row is not None
                else None
            )
            candidates = {item.id: item for item in rows}
            rollback = (
                self._optional_publication(current.base_release_id)
                if current is not None
                else None
            )
            if rollback is None or not self._has_recorded_release(rollback):
                rollback = next(
                    (
                        item
                        for item in sorted(rows, key=lambda value: value.generation, reverse=True)
                        if current is not None
                        and item.id != current.id
                        and item.generation < current.generation
                        and self._has_recorded_release(item)
                    ),
                    None,
                )
            if current is not None:
                candidates[current.id] = current
            contexts[gateway_id] = {
                "gateway_name": gateway_names.get(gateway_id, "Unknown gateway"),
                "current_id": current.id if current else None,
                "rollback_id": rollback.id if rollback else None,
            }
        return contexts

    def _release_retention(
        self,
        publications: Sequence[GatewayPublication],
        contexts: Mapping[UUID, Mapping[str, object]],
    ) -> dict[UUID, dict[str, object]]:
        protections = {
            UUID(str(row["publication_id"])): row
            for row in self._repository.list_gateway_release_protections(
                [publication.id for publication in publications]
            )
        }
        grouped: dict[UUID, list[GatewayPublication]] = {}
        for publication in publications:
            grouped.setdefault(publication.gateway_profile_id, []).append(publication)
        now = datetime.now(UTC)
        retained: dict[UUID, dict[str, object]] = {}
        for gateway_id, rows in grouped.items():
            context = contexts[gateway_id]
            promoted = sorted(
                (
                    row
                    for row in rows
                    if row.status
                    in {
                        PublicationStatus.ACTIVE,
                        PublicationStatus.SUPERSEDED,
                        PublicationStatus.ROLLED_BACK,
                    }
                ),
                key=lambda row: row.generation,
                reverse=True,
            )
            count_ids = {
                row.id for row in promoted[: self._retention_policy.retained_count]
            }
            operation_ids: set[UUID] = set()
            for operation in self._repository.list_gateway_release_operations(
                gateway_id, 1000
            ):
                if str(operation["status"]) in {"succeeded", "failed", "restored"}:
                    continue
                for key in ("target_release_id", "prior_release_id"):
                    if operation.get(key) is not None:
                        operation_ids.add(UUID(str(operation[key])))
            for publication in rows:
                protection = protections.get(publication.id)
                reasons: list[str] = []
                if publication.id == context["current_id"]:
                    reasons.append("current")
                if publication.id == context["rollback_id"]:
                    reasons.append("immediate_rollback")
                if protection is not None and bool(protection["pinned"]):
                    reasons.append("pinned")
                label = (
                    str(protection["protected_label"])
                    if protection is not None
                    and protection.get("protected_label") is not None
                    else None
                )
                if label is not None and label in self._retention_policy.protected_labels:
                    reasons.append(f"protected_label:{label}")
                retain_until = protection.get("retain_until") if protection else None
                if isinstance(retain_until, datetime) and retain_until > now:
                    reasons.append("retain_until")
                if publication.id in count_ids:
                    reasons.append("retained_count")
                age_limit = (
                    self._retention_policy.failed_retained_days
                    if publication.status is PublicationStatus.FAILED
                    else self._retention_policy.retained_days
                )
                if publication.created_at >= now - timedelta(days=age_limit):
                    reasons.append("retained_age")
                if publication.id in operation_ids:
                    reasons.append("active_operation")
                retained[publication.id] = {
                    "pinned": bool(protection and protection["pinned"]),
                    "protected_label": label,
                    "retain_until": retain_until,
                    "retention_eligible": bool(reasons),
                    "retention_reasons": reasons,
                }
        return retained

    @staticmethod
    def _has_recorded_release(publication: GatewayPublication) -> bool:
        return bool(publication.apim_revision and publication.policy_sha256)

    def _release_summary(
        self,
        publication: GatewayPublication,
        context: Mapping[str, object],
        retention: Mapping[str, object],
        against: GatewayPublication | None,
        dependencies: GatewayReleaseDependencies,
    ) -> GatewayReleaseSummary:
        current_id = context["current_id"]
        rollback_id = context["rollback_id"]
        raw_retention_reasons = retention["retention_reasons"]
        retention_reasons = (
            [str(value) for value in raw_retention_reasons]
            if isinstance(raw_retention_reasons, Sequence)
            and not isinstance(raw_retention_reasons, str)
            else []
        )
        role = (
            GatewayReleaseRole.CURRENT
            if publication.id == current_id
            else GatewayReleaseRole.IMMEDIATE_ROLLBACK
            if publication.id == rollback_id
            else GatewayReleaseRole.PINNED
            if retention["pinned"]
            else GatewayReleaseRole.EXPIRED
            if not retention["retention_eligible"]
            and publication.status
            in {
                PublicationStatus.ACTIVE,
                PublicationStatus.SUPERSEDED,
                PublicationStatus.FAILED,
                PublicationStatus.ROLLED_BACK,
            }
            else GatewayReleaseRole.FAILED
            if publication.status is PublicationStatus.FAILED
            else GatewayReleaseRole.ROLLED_BACK
            if publication.status is PublicationStatus.ROLLED_BACK
            else GatewayReleaseRole.IN_PROGRESS
            if publication.status not in {PublicationStatus.ACTIVE, PublicationStatus.SUPERSEDED}
            else GatewayReleaseRole.SUPERSEDED
        )
        view = GatewayPublicationView.from_publication(publication)
        blockers = self._rollback_blockers(
            publication,
            dependencies,
            is_current=role is GatewayReleaseRole.CURRENT,
        )
        return GatewayReleaseSummary(
            id=publication.id,
            gateway_profile_id=publication.gateway_profile_id,
            gateway_name=str(context["gateway_name"]),
            generation=publication.generation,
            role=role,
            publication_kind=publication.publication_kind,
            model_key=view.model_key,
            display_name=view.display_name,
            status=publication.status,
            apim_revision=publication.apim_revision,
            policy_sha256=publication.policy_sha256,
            desired_spec_sha256=publication.desired_spec_sha256,
            change_set=self._release_changes(publication, against),
            dependency_count=(
                len(dependencies.backends)
                + len(dependencies.backend_pools)
                + len(dependencies.named_values)
            ),
            recorded_dependencies_complete=dependencies.recorded_complete,
            live_integrity_status=dependencies.live_status,
            rollback_eligible=not blockers,
            rollback_blockers=blockers,
            pinned=bool(retention["pinned"]),
            protected_label=(
                str(retention["protected_label"])
                if retention["protected_label"] is not None
                else None
            ),
            retain_until=(
                retention["retain_until"]
                if isinstance(retention["retain_until"], datetime)
                else None
            ),
            retention_eligible=bool(retention["retention_eligible"]),
            retention_reasons=retention_reasons,
            created_by=publication.created_by,
            created_at=publication.created_at,
            completed_at=publication.completed_at,
        )

    @staticmethod
    def _release_changes(
        release: GatewayPublication, against: GatewayPublication | None
    ) -> GatewayReleaseChangeSet:
        before = against.desired_spec.bindings if against else []
        after = release.desired_spec.bindings
        before_by_model = {item.model.model_key.casefold(): item for item in before}
        after_by_model = {item.model.model_key.casefold(): item for item in after}
        before_keys = set(before_by_model)
        after_keys = set(after_by_model)
        shared = before_keys & after_keys

        def pool(binding: GatewayModelBinding) -> object | None:
            configured = binding.backend_pool
            return configured.model_dump(mode="json") if configured is not None else None

        before_pools = {key: pool(value) for key, value in before_by_model.items() if pool(value)}
        after_pools = {key: pool(value) for key, value in after_by_model.items() if pool(value)}
        before_pool_keys = set(before_pools)
        after_pool_keys = set(after_pools)
        return GatewayReleaseChangeSet(
            added_models=sorted(
                after_by_model[key].model.model_key for key in after_keys - before_keys
            ),
            removed_models=sorted(
                before_by_model[key].model.model_key for key in before_keys - after_keys
            ),
            changed_models=sorted(
                after_by_model[key].model.model_key
                for key in shared
                if before_by_model[key].model_dump(mode="json")
                != after_by_model[key].model_dump(mode="json")
            ),
            added_backend_pools=sorted(
                after_by_model[key].model.model_key for key in after_pool_keys - before_pool_keys
            ),
            removed_backend_pools=sorted(
                before_by_model[key].model.model_key for key in before_pool_keys - after_pool_keys
            ),
            changed_backend_pools=sorted(
                after_by_model[key].model.model_key
                for key in before_pool_keys & after_pool_keys
                if before_pools[key] != after_pools[key]
            ),
        )

    def _release_dependencies(
        self, publication: GatewayPublication
    ) -> GatewayReleaseDependencies:
        snapshot = self._repository.latest_gateway_release_integrity_snapshot(
            publication.id
        )
        return self._release_dependencies_from_snapshot(publication, snapshot)

    @staticmethod
    def _release_dependencies_from_snapshot(
        publication: GatewayPublication,
        snapshot: Mapping[str, Any] | None,
    ) -> GatewayReleaseDependencies:
        manifest = publication.resource_manifest
        raw_backends = manifest.get("backends")
        raw_named_values = manifest.get("named_values")
        backend_ids = (
            [str(value) for value in raw_backends]
            if isinstance(raw_backends, list)
            else []
        )
        named_values = (
            [str(value) for value in raw_named_values]
            if isinstance(raw_named_values, list)
            else []
        )
        pools = sorted(value for value in backend_ids if value.startswith("turnstile-pool-"))
        backends = sorted(value for value in backend_ids if value not in pools)
        required = {
            "apim_revision": publication.apim_revision,
            "compiled_policy_sha256": publication.policy_sha256,
            "base_apim_revision": manifest.get("base_apim_revision"),
            "parent_policy_sha256": (
                manifest.get("parent_policy_sha256")
                or manifest.get("base_policy_sha256")
            ),
            "backends": raw_backends if isinstance(raw_backends, list) else None,
            "named_values": raw_named_values if isinstance(raw_named_values, list) else None,
        }
        issues = [f"missing_recorded_{key}" for key, value in required.items() if value is None]
        recorded = GatewayReleaseDependencies(
            apim_revision=publication.apim_revision,
            parent_policy_sha256=(
                str(
                    manifest.get("parent_policy_sha256")
                    or manifest["base_policy_sha256"]
                )
                if manifest.get("parent_policy_sha256") is not None
                or manifest.get("base_policy_sha256") is not None
                else None
            ),
            compiled_policy_sha256=publication.policy_sha256,
            backends=backends,
            backend_pools=pools,
            named_values=sorted(named_values),
            recorded_complete=not issues,
            live_status="not_checked",
            issues=issues,
        )
        if snapshot is None:
            return recorded
        try:
            checked = GatewayReleaseDependencies.model_validate(
                snapshot["dependencies"]
            )
        except (TypeError, ValueError):
            return recorded.model_copy(
                update={
                    "live_status": "mismatched",
                    "live_checked_at": snapshot["checked_at"],
                    "issues": [*issues, "invalid_integrity_snapshot"],
                }
            )
        identity_fields = (
            "apim_revision",
            "parent_policy_sha256",
            "compiled_policy_sha256",
            "backends",
            "backend_pools",
            "named_values",
        )
        if any(
            getattr(recorded, key) != getattr(checked, key) for key in identity_fields
        ):
            return recorded.model_copy(
                update={
                    "live_status": "mismatched",
                    "live_checked_at": snapshot["checked_at"],
                    "issues": [*issues, "integrity_snapshot_identity_mismatch"],
                }
            )
        return checked.model_copy(
            update={
                "live_status": snapshot["status"],
                "live_checked_at": snapshot["checked_at"],
                "issues": [str(value) for value in snapshot["issues"]],
            }
        )

    @staticmethod
    def _rollback_blockers(
        publication: GatewayPublication,
        dependencies: GatewayReleaseDependencies,
        *,
        is_current: bool,
    ) -> list[str]:
        blockers: list[str] = []
        if is_current:
            blockers.append("release_is_current")
        if publication.status not in {PublicationStatus.ACTIVE, PublicationStatus.SUPERSEDED}:
            blockers.append("release_was_not_promoted")
        if not dependencies.recorded_complete:
            blockers.append("recorded_dependencies_incomplete")
        if dependencies.live_status != "healthy":
            blockers.append("live_dependency_check_required")
        if publication.resource_manifest.get("rollback_invalid") is True:
            blockers.append("release_marked_invalid_for_rollback")
        return blockers

    def reconcile_gateway(
        self, gateway_profile_id: UUID, created_by: str
    ) -> GatewayPublication:
        registry = self._repository.registry()
        gateway = self._find(registry["gateways"], gateway_profile_id)
        if gateway is None or not gateway["enabled"] or gateway["implementation"] != "apim":
            raise ControlPlaneConflictError("The selected APIM gateway is unavailable")
        effective_row = self._repository.effective_gateway_publication(gateway_profile_id)
        if effective_row is None:
            raise ControlPlaneConflictError("The gateway has no effective release to reconcile")
        effective = GatewayPublication.model_validate(effective_row)
        reconciled = self._reconciled_release_spec(effective.desired_spec, registry)
        desired_spec, reconciled_sha256 = self._release_payload(reconciled)
        desired_hash = hashlib.sha256(
            f"route_reconcile:{effective.id}:{reconciled_sha256}".encode()
        ).hexdigest()
        try:
            row = self._repository.create_gateway_publication(
                gateway_profile_id,
                desired_spec,
                desired_hash,
                PublicationKind.ROUTE_RECONCILE.value,
                created_by,
                expected_base_release_id=effective.id,
            )
        except ValueError as error:
            raise ControlPlaneConflictError(str(error)) from error
        return GatewayPublication.model_validate(row)

    def configure_model_backend_pool(
        self,
        model_id: UUID,
        write: GatewayBackendPoolWrite,
        created_by: str,
    ) -> GatewayPublication:
        registry = self._repository.registry()
        model = self._find(registry["models"], model_id)
        if model is None or not model["enabled"]:
            raise ControlPlaneNotFoundError("Model not found")
        primary_runtime = self._find(registry["runtimes"], model["runtime_id"])
        if primary_runtime is None or primary_runtime.get("gateway_profile_id") is None:
            raise ControlPlaneConflictError(
                "The model is not routed through a managed APIM gateway"
            )
        gateway_id = UUID(str(primary_runtime["gateway_profile_id"]))
        effective_row = self._repository.effective_gateway_publication(gateway_id)
        if effective_row is None:
            raise ControlPlaneConflictError("The gateway has no effective release")
        effective = GatewayPublication.model_validate(effective_row)
        reconciled = self._reconciled_release_spec(effective.desired_spec, registry)
        alias = str(model["model_key"]).casefold()
        target = next(
            (
                binding
                for binding in reconciled.bindings
                if binding.model.model_key.casefold() == alias
            ),
            None,
        )
        if target is None or not target.routing_managed:
            raise ControlPlaneConflictError(
                "The model has no managed APIM binding in the effective release"
            )
        target = target.model_copy(
            update={
                "provider_id": model["provider_id"],
                "runtime_id": primary_runtime["id"],
            }
        )
        members = [
            self._backend_pool_member(
                target,
                item,
                model,
                registry["runtimes"],
                registry["models"],
                gateway_id,
            )
            for item in write.members
        ]
        pool = GatewayBackendPoolConfig(
            members=members,
            rate_limit=write.rate_limit,
            session_affinity=write.session_affinity,
        )
        runtime_config = {
            **target.runtime_config,
            "apim_backend_pool": pool.model_dump(mode="json", exclude={"session_affinity"}),
        }
        runtime_config.pop("apim_backend_pool_session_affinity", None)
        if pool.session_affinity:
            runtime_config["apim_backend_pool_session_affinity"] = True
        updated_target = target.model_copy(
            update={"runtime_config": runtime_config}
        )
        updated_bindings = [
            updated_target
            if binding.model.model_key.casefold() == alias
            else binding
            for binding in reconciled.bindings
        ]
        updated = reconciled.model_copy(update={"bindings": updated_bindings})
        return self._queue_route_reconcile(effective, updated, created_by)

    def model_backend_pool(self, model_id: UUID) -> GatewayBackendPoolConfig:
        registry = self._repository.registry()
        model = self._find(registry["models"], model_id)
        if model is None or not model["enabled"]:
            raise ControlPlaneNotFoundError("Model not found")
        runtime = self._find(registry["runtimes"], model["runtime_id"])
        if runtime is None or runtime.get("gateway_profile_id") is None:
            raise ControlPlaneNotFoundError("Model backend pool not found")
        effective_row = self._repository.effective_gateway_publication(
            UUID(str(runtime["gateway_profile_id"]))
        )
        if effective_row is None:
            raise ControlPlaneNotFoundError("Model backend pool not found")
        effective = GatewayPublication.model_validate(effective_row)
        alias = str(model["model_key"]).casefold()
        binding = next(
            (
                item
                for item in effective.desired_spec.bindings
                if item.model.model_key.casefold() == alias
            ),
            None,
        )
        pool = binding.backend_pool if binding is not None else None
        if pool is None:
            raise ControlPlaneNotFoundError("Model backend pool not found")
        return pool

    def remove_model_backend_pool(
        self,
        model_id: UUID,
        created_by: str,
    ) -> GatewayPublication:
        registry = self._repository.registry()
        model = self._find(registry["models"], model_id)
        if model is None or not model["enabled"]:
            raise ControlPlaneNotFoundError("Model not found")
        runtime = self._find(registry["runtimes"], model["runtime_id"])
        if runtime is None or runtime.get("gateway_profile_id") is None:
            raise ControlPlaneConflictError(
                "The model is not routed through a managed APIM gateway"
            )
        gateway_id = UUID(str(runtime["gateway_profile_id"]))
        effective_row = self._repository.effective_gateway_publication(gateway_id)
        if effective_row is None:
            raise ControlPlaneConflictError("The gateway has no effective release")
        effective = GatewayPublication.model_validate(effective_row)
        reconciled = self._reconciled_release_spec(effective.desired_spec, registry)
        alias = str(model["model_key"]).casefold()
        target = next(
            (
                binding
                for binding in reconciled.bindings
                if binding.model.model_key.casefold() == alias
            ),
            None,
        )
        if target is None:
            raise ControlPlaneConflictError(
                "The model has no APIM binding in the effective release"
            )
        if "apim_backend_pool" not in target.runtime_config:
            raise ControlPlaneConflictError("The model has no APIM backend pool")
        runtime_config = dict(target.runtime_config)
        runtime_config.pop("apim_backend_pool")
        runtime_config.pop("apim_backend_pool_session_affinity", None)
        updated_target = target.model_copy(
            update={"runtime_config": runtime_config}
        )
        updated = reconciled.model_copy(
            update={
                "bindings": [
                    updated_target if binding is target else binding
                    for binding in reconciled.bindings
                ]
            }
        )
        return self._queue_route_reconcile(effective, updated, created_by)

    def _queue_route_reconcile(
        self,
        effective: GatewayPublication,
        spec: GatewayReleaseSpec,
        created_by: str,
    ) -> GatewayPublication:
        normalized = self._normalize_release_spec(spec)
        desired_spec, reconciled_sha256 = self._release_payload(normalized)
        desired_hash = hashlib.sha256(
            f"route_reconcile:{effective.id}:{reconciled_sha256}".encode()
        ).hexdigest()
        try:
            row = self._repository.create_gateway_publication(
                spec.gateway_profile_id,
                desired_spec,
                desired_hash,
                PublicationKind.ROUTE_RECONCILE.value,
                created_by,
                expected_base_release_id=effective.id,
            )
        except ValueError as error:
            raise ControlPlaneConflictError(str(error)) from error
        return GatewayPublication.model_validate(row)

    def _backend_pool_member(
        self,
        target: GatewayModelBinding,
        member: GatewayBackendPoolMemberWrite,
        model: Mapping[str, Any],
        runtimes: Sequence[dict[str, Any]],
        models: Sequence[dict[str, Any]],
        gateway_id: UUID,
    ) -> GatewayBackendPoolMember:
        runtime = self._find(runtimes, member.runtime_id)
        if runtime is None or not runtime["enabled"]:
            raise ControlPlaneConflictError(
                f"Backend pool runtime {member.runtime_id} is unavailable"
            )
        if (
            runtime.get("provider_id") != model.get("provider_id")
            or runtime.get("gateway_profile_id") != gateway_id
        ):
            raise ControlPlaneConflictError(
                "Every backend pool runtime must use the model provider and APIM gateway"
            )
        upstream_model_id = str(
            model.get("upstream_model_id") or model["model_key"]
        ).strip().casefold()
        has_equivalent_deployment = any(
            candidate.get("enabled") is True
            and candidate.get("provider_id") == model.get("provider_id")
            and candidate.get("runtime_id") == runtime["id"]
            and str(
                candidate.get("upstream_model_id") or candidate["model_key"]
            ).strip().casefold()
            == upstream_model_id
            for candidate in models
        )
        if not has_equivalent_deployment:
            raise ControlPlaneConflictError(
                "Every backend pool runtime must have an enabled equivalent model deployment"
            )
        config = dict(runtime.get("config") or {})
        if not config.get("control_plane_managed"):
            raise ControlPlaneConflictError(
                "Every backend pool runtime must be managed by the control plane"
            )
        runtime_brand = BrandKey(
            runtime.get("brand_key", target.runtime_brand_key)
        )
        raw_api_format = str(config.get("api_format") or "")
        api_format = (
            ApiFormat.ANTHROPIC_MESSAGES
            if raw_api_format == ApiFormat.ANTHROPIC_MESSAGES.value
            or runtime_brand in {
                BrandKey.AMAZON_BEDROCK,
                BrandKey.AZURE_DATABRICKS,
            }
            else ApiFormat.OPENAI_CHAT
        )
        backend_url = config.get("backend_url")
        backend_path = config.get("backend_path")
        if not backend_url or not backend_path:
            raise ControlPlaneConflictError(
                "Every backend pool runtime needs a managed backend URL and path"
            )
        candidate = GatewayModelBinding.model_validate(
            {
                **target.model_dump(mode="python"),
                "runtime_id": runtime["id"],
                "runtime_name": runtime["name"],
                "runtime_kind": runtime["runtime_kind"],
                "runtime_brand_key": runtime_brand,
                "api_format": api_format,
                "backend_url": backend_url,
                "backend_path": backend_path,
                "auth_strategy": config.get("auth_strategy", AuthStrategy.NONE),
                "named_value_name": config.get("named_value_name"),
                "key_vault_secret_id": config.get("key_vault_secret_id"),
                "managed_identity_resource": config.get(
                    "managed_identity_resource"
                ),
                "streaming_mode": config.get(
                    "streaming_mode",
                    StreamingMode.NATIVE,
                ),
                "runtime_config": config,
            }
        )
        candidate_spec = GatewayReleaseSpec(
            gateway_profile_id=gateway_id,
            discovery_models=[
                DiscoveryModel(
                    id=target.model.model_key,
                    display_name=target.model.display_name,
                    api_format=api_format,
                )
            ],
            bindings=[candidate],
        )
        normalized = self._normalize_release_spec(candidate_spec).bindings[0]
        invariant_fields = ("api_format", "backend_path", "streaming_mode")
        if any(
            getattr(normalized, field) != getattr(target, field)
            for field in invariant_fields
        ):
            raise ControlPlaneConflictError(
                "Backend pool runtimes must share protocol, path, and streaming"
            )
        named_auth = {
            AuthStrategy.NAMED_VALUE_BEARER,
            AuthStrategy.NAMED_VALUE_API_KEY,
        }
        if normalized.auth_strategy in named_auth and (
            not normalized.named_value_name
            or config.get("credential_provisioned") is not True
        ):
            raise ControlPlaneConflictError(
                "A named-value pool member requires a provisioned credential"
            )
        if target.auth_strategy is AuthStrategy.MANAGED_IDENTITY:
            auth_compatible = (
                normalized.auth_strategy is AuthStrategy.NAMED_VALUE_API_KEY
                or (
                    normalized.auth_strategy is AuthStrategy.MANAGED_IDENTITY
                    and normalized.managed_identity_resource
                    == target.managed_identity_resource
                )
            )
        else:
            auth_compatible = normalized.auth_strategy is target.auth_strategy
        if not auth_compatible:
            raise ControlPlaneConflictError(
                "Backend pool runtime authentication cannot be applied per member"
            )
        if normalized.backend_url is None:
            raise ControlPlaneConflictError(
                "The normalized backend pool runtime has no backend URL"
            )
        return GatewayBackendPoolMember(
            runtime_id=runtime["id"],
            runtime_name=str(runtime["name"]),
            backend_url=normalized.backend_url,
            auth_strategy=normalized.auth_strategy,
            named_value_name=normalized.named_value_name,
            priority=member.priority,
            weight=member.weight,
        )

    @classmethod
    def _reconciled_release_spec(
        cls,
        effective: GatewayReleaseSpec,
        registry: Mapping[str, Sequence[dict[str, Any]]],
    ) -> GatewayReleaseSpec:
        bindings = list(effective.bindings)
        bound_aliases = {binding.model.model_key.casefold() for binding in bindings}
        discovery_aliases = {item.id.casefold() for item in effective.discovery_models}
        templates_by_runtime: dict[UUID, list[GatewayModelBinding]] = {}
        for binding in bindings:
            if binding.routing_managed and binding.runtime_id is not None:
                templates_by_runtime.setdefault(binding.runtime_id, []).append(binding)

        additions: list[GatewayModelBinding] = []
        for model in registry["models"]:
            alias = str(model["model_key"]).casefold()
            runtime_id = model.get("runtime_id")
            if (
                not model["enabled"]
                or alias in bound_aliases
                or alias not in discovery_aliases
                or runtime_id not in templates_by_runtime
            ):
                continue
            family = ModelFamilyKey(model.get("family_key", ModelFamilyKey.GENERIC))
            templates = templates_by_runtime[runtime_id]
            template = next(
                (
                    item
                    for item in templates
                    if family is ModelFamilyKey.CLAUDE
                    or item.api_format is ApiFormat.OPENAI_CHAT
                ),
                None,
            )
            if template is None:
                continue
            target = ModelTarget(
                model_key=model["model_key"],
                display_name=model["display_name"],
                upstream_model_id=model.get("upstream_model_id") or model["model_key"],
                family_key=family,
                capabilities=list(model.get("capabilities") or ["chat"]),
                context_window=model.get("context_window"),
                input_cost_per_million=model.get("input_cost_per_million"),
                output_cost_per_million=model.get("output_cost_per_million"),
                cached_cost_per_million=model.get("cached_cost_per_million"),
                cache_write_cost_per_million=model.get("cache_write_cost_per_million"),
                allowed_roles=list(model.get("allowed_roles") or ["owner", "admin", "member"]),
                assignment_required=bool(model.get("assignment_required", False)),
            )
            runtime_config = dict(template.runtime_config)
            runtime_config.pop("apim_backend_pool", None)
            runtime_config.pop("apim_backend_pool_session_affinity", None)
            additions.append(
                template.model_copy(
                    update={
                        "model": target,
                        "runtime_config": runtime_config,
                    }
                )
            )
            bound_aliases.add(alias)

        reconciled = effective.model_copy(
            update={
                "bindings": [
                    *sorted(additions, key=lambda item: item.model.model_key.casefold()),
                    *bindings,
                ]
            }
        )
        return cls._normalize_release_spec(reconciled)

    def retry(
        self,
        publication_id: UUID,
        write: GatewayPublicationRetry,
        created_by: str,
    ) -> GatewayPublication:
        publication = self.publication(publication_id)
        if publication.status.value != "failed":
            raise ControlPlaneConflictError("Only a failed publication can be retried")
        if publication.publication_kind is PublicationKind.MODEL_REMOVE:
            raise ControlPlaneConflictError(
                "Retry model deletion by submitting the delete request again"
            )
        if publication.publication_kind is PublicationKind.ROUTE_RECONCILE:
            raise ControlPlaneConflictError(
                "Submit a fresh route reconciliation against the effective release"
            )
        binding = publication.desired_spec.bindings[-1]
        accepts_credential = (
            binding.auth_strategy
            in {
                AuthStrategy.NAMED_VALUE_BEARER,
                AuthStrategy.NAMED_VALUE_API_KEY,
            }
            and binding.key_vault_secret_id is None
        )
        if accepts_credential and not binding.named_value_name:
            raise ControlPlaneConflictError(
                "This publication does not use a directly managed API key"
            )
        requires_credential = publication_retry_requires_credential(publication)
        if write.api_key is not None:
            if not accepts_credential:
                raise ControlPlaneConflictError("This publication does not accept an API key")
            if self._cipher is None:
                raise ControlPlaneConflictError("Credential encryption is unavailable")
            encrypted = self._cipher.encrypt(write.api_key.get_secret_value())
        else:
            if requires_credential:
                raise ControlPlaneConflictError("A replacement API key is required")
            encrypted = None
        normalized_spec = self._normalize_release_spec(publication.desired_spec)
        desired_spec, desired_hash = self._release_payload(normalized_spec)
        try:
            row = self._repository.requeue_gateway_publication(
                publication_id,
                created_by,
                encrypted,
                desired_spec,
                desired_hash,
            )
        except ValueError as error:
            raise ControlPlaneConflictError(str(error)) from error
        return GatewayPublication.model_validate(row)

    def resume_authorization(
        self,
        publication_id: UUID,
        created_by: str,
    ) -> GatewayPublication:
        publication = self.publication(publication_id)
        if publication.status.value != "awaiting_authorization":
            raise ControlPlaneConflictError(
                "The publication is not waiting for provider authorization"
            )
        try:
            row = self._repository.resume_gateway_publication_authorization(
                publication_id, created_by
            )
        except ValueError as error:
            raise ControlPlaneConflictError(str(error)) from error
        return GatewayPublication.model_validate(row)

    def cancel_authorization(
        self,
        publication_id: UUID,
        created_by: str,
    ) -> GatewayPublication:
        publication = self.publication(publication_id)
        if publication.status.value == "queued":
            if publication.attempt_count != 0 or publication.started_at is not None:
                raise ControlPlaneConflictError(
                    "Only an unstarted queued publication can be cancelled"
                )
            row = self._repository.cancel_unstarted_gateway_publication(
                publication_id, created_by
            )
            if row is None:
                raise ControlPlaneConflictError(
                    "The publication changed before it could be cancelled"
                )
            return GatewayPublication.model_validate(row)
        if publication.status.value != "awaiting_authorization":
            raise ControlPlaneConflictError(
                "Only an unstarted queued publication or a publication waiting "
                "for provider authorization can be cancelled"
            )
        row = self._repository.transition_gateway_publication(
            publication_id,
            "awaiting_authorization",
            "rolled_back",
            {
                "error_code": "cancelled_by_owner",
                "error_message": "Publication cancelled before promotion",
            },
            created_by,
        )
        if row is None:
            raise ControlPlaneConflictError(
                "The publication changed before it could be cancelled"
            )
        return GatewayPublication.model_validate(row)

    def publish(
        self, write: GatewayPublicationCreate, created_by: str
    ) -> GatewayPublication:
        registry = self._repository.registry()
        gateway = self._find(registry["gateways"], write.gateway_profile_id)
        if gateway is None or not gateway["enabled"] or gateway["implementation"] != "apim":
            raise ControlPlaneConflictError("The selected APIM gateway is unavailable")

        provider = self._resolve_provider(write, registry["providers"])
        runtime = self._resolve_runtime(write, provider, registry["runtimes"])
        self._validate_connection(provider, runtime)
        model = self._release_model(write.model, provider, runtime)
        self._validate_model(model, registry["models"])
        binding_runtime = self._model_binding_runtime(provider, runtime, model)

        previous_row = self._repository.effective_gateway_publication(
            write.gateway_profile_id
        )
        previous = (
            GatewayPublication.model_validate(previous_row)
            if previous_row is not None
            else None
        )
        previous_spec = (
            self._reconciled_release_spec(previous.desired_spec, registry)
            if previous
            else None
        )
        bindings = list(previous_spec.bindings) if previous_spec else []
        binding = GatewayModelBinding(
            provider_id=provider.get("id"),
            provider_name=str(provider["name"]),
            provider_kind=provider["provider_kind"],
            provider_brand_key=provider["brand_key"],
            provider_config=dict(provider.get("config") or {}),
            runtime_id=binding_runtime.get("id"),
            routing_managed=bool(binding_runtime["routing_managed"]),
            runtime_name=str(binding_runtime["name"]),
            runtime_kind=binding_runtime["runtime_kind"],
            runtime_brand_key=binding_runtime["brand_key"],
            api_format=binding_runtime["api_format"],
            backend_url=binding_runtime.get("backend_url"),
            backend_path=str(binding_runtime["backend_path"]),
            auth_strategy=binding_runtime["auth_strategy"],
            named_value_name=binding_runtime.get("named_value_name"),
            key_vault_secret_id=binding_runtime.get("key_vault_secret_id"),
            managed_identity_resource=binding_runtime.get("managed_identity_resource"),
            streaming_mode=binding_runtime["streaming_mode"],
            runtime_config=dict(binding_runtime.get("config") or {}),
            model=model,
        )
        bindings.append(binding)

        discovery = list(previous_spec.discovery_models) if previous_spec else []
        discovery.extend(self._existing_discovery(write.gateway_profile_id, registry))
        discovery.append(
            DiscoveryModel(
                id=model.model_key,
                display_name=model.display_name,
                api_format=binding_runtime["api_format"],
            )
        )
        unique_discovery = {
            item.id.casefold(): item for item in discovery
        }
        spec = GatewayReleaseSpec(
            gateway_profile_id=write.gateway_profile_id,
            discovery_models=sorted(unique_discovery.values(), key=lambda item: item.id),
            bindings=bindings,
        )
        credential_ciphertext = None
        if write.runtime.api_key is not None:
            if self._cipher is None:
                raise ControlPlaneConflictError("Credential encryption is unavailable")
            credential_ciphertext = self._cipher.encrypt(
                write.runtime.api_key.get_secret_value()
            )
        return self._queue_release(
            spec,
            PublicationKind.MODEL_ADD,
            created_by,
            credential_ciphertext,
        )

    def remove_model(self, model_id: UUID, created_by: str) -> GatewayPublication:
        registry = self._repository.registry()
        model = self._find(registry["models"], model_id)
        if model is None:
            raise ControlPlaneNotFoundError("Model not found")
        if model["is_default"]:
            raise ControlPlaneConflictError(
                "Select another default model before deleting this model"
            )
        runtime = self._find(registry["runtimes"], model["runtime_id"])
        if runtime is None or runtime.get("gateway_profile_id") is None:
            raise ControlPlaneConflictError(
                "Only models routed through an APIM gateway can be deleted"
            )
        gateway_id = runtime["gateway_profile_id"]
        gateway = self._find(registry["gateways"], gateway_id)
        if gateway is None or gateway["implementation"] != "apim":
            raise ControlPlaneConflictError(
                "The model is not routed through a managed APIM gateway"
            )

        previous_row = self._repository.effective_gateway_publication(gateway_id)
        previous = (
            GatewayPublication.model_validate(previous_row)
            if previous_row is not None
            else None
        )
        alias = str(model["model_key"]).casefold()
        bindings = [
            binding
            for binding in (previous.desired_spec.bindings if previous else [])
            if binding.model.model_key.casefold() != alias
        ]
        discovery = list(previous.desired_spec.discovery_models) if previous else []
        discovery.extend(self._existing_discovery(gateway_id, registry))
        remaining = {
            item.id.casefold(): item
            for item in discovery
            if item.id.casefold() != alias
        }
        spec = GatewayReleaseSpec(
            gateway_profile_id=gateway_id,
            discovery_models=sorted(remaining.values(), key=lambda item: item.id),
            bindings=bindings,
            removed_models=[
                ModelRemovalTarget(
                    model_id=model["id"],
                    model_key=model["model_key"],
                    display_name=model["display_name"],
                    api_format=self._model_api_format(model, runtime),
                )
            ],
        )
        return self._queue_release(
            spec,
            PublicationKind.MODEL_REMOVE,
            created_by,
            None,
        )

    def rotate_credential(
        self,
        gateway_profile_id: UUID,
        write: GatewayCredentialRotation,
        created_by: str,
    ) -> GatewayPublication:
        previous_row = self._repository.effective_gateway_publication(
            gateway_profile_id
        )
        if previous_row is None:
            raise ControlPlaneConflictError("The gateway has no active publication")
        previous = GatewayPublication.model_validate(previous_row)
        target = next(
            (
                binding
                for binding in previous.desired_spec.bindings
                if binding.model.model_key.casefold() == write.model_key.casefold()
            ),
            None,
        )
        if target is None:
            raise ControlPlaneConflictError("The model is not managed by this gateway release")
        if (
            not target.routing_managed
            or target.auth_strategy
            not in {
                AuthStrategy.NAMED_VALUE_BEARER,
                AuthStrategy.NAMED_VALUE_API_KEY,
            }
            or target.key_vault_secret_id is not None
        ):
            raise ControlPlaneConflictError(
                "The model does not use a directly managed API key"
            )
        if self._cipher is None:
            raise ControlPlaneConflictError("Credential encryption is unavailable")
        named_value_name = f"finops-credential-{uuid4().hex[:20]}"
        def same_runtime(binding: GatewayModelBinding) -> bool:
            return (
                binding.runtime_name.casefold() == target.runtime_name.casefold()
                and str(binding.backend_url).rstrip("/")
                == str(target.backend_url).rstrip("/")
            )

        updated = [
            binding.model_copy(update={"named_value_name": named_value_name})
            if same_runtime(binding)
            else binding
            for binding in previous.desired_spec.bindings
        ]
        target_binding = next(
            binding
            for binding in updated
            if binding.model.model_key.casefold() == write.model_key.casefold()
        )
        bindings = [binding for binding in updated if binding is not target_binding]
        bindings.append(target_binding)
        registry = self._repository.registry()
        discovery = list(previous.desired_spec.discovery_models)
        discovery.extend(self._existing_discovery(gateway_profile_id, registry))
        unique_discovery = {item.id.casefold(): item for item in discovery}
        spec = GatewayReleaseSpec(
            gateway_profile_id=gateway_profile_id,
            discovery_models=sorted(unique_discovery.values(), key=lambda item: item.id),
            bindings=bindings,
        )
        encrypted = self._cipher.encrypt(write.api_key.get_secret_value())
        return self._queue_release(
            spec,
            PublicationKind.CREDENTIAL_ROTATION,
            created_by,
            encrypted,
        )

    def _queue_release(
        self,
        spec: GatewayReleaseSpec,
        publication_kind: PublicationKind,
        created_by: str,
        credential_ciphertext: bytes | None,
    ) -> GatewayPublication:
        payload, digest = self._release_payload(spec)
        try:
            row = self._repository.create_gateway_publication(
                spec.gateway_profile_id,
                payload,
                digest,
                publication_kind.value,
                created_by,
                credential_ciphertext,
            )
        except ValueError as error:
            raise ControlPlaneConflictError(str(error)) from error
        return GatewayPublication.model_validate(row)

    @staticmethod
    def _release_payload(spec: GatewayReleaseSpec) -> tuple[dict[str, Any], str]:
        payload = spec.model_dump(mode="json")
        canonical = json.dumps(
            payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        return payload, hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_release_spec(spec: GatewayReleaseSpec) -> GatewayReleaseSpec:
        bindings: list[GatewayModelBinding] = []
        for binding in spec.bindings:
            if (
                binding.provider_brand_key is BrandKey.MICROSOFT_FOUNDRY
                and binding.model.family_key is ModelFamilyKey.CLAUDE
            ):
                config = dict(binding.runtime_config)
                endpoint = urlsplit(
                    str(config.get("project_endpoint") or binding.backend_url or "")
                )
                if endpoint.scheme != "https" or not (
                    endpoint.hostname or ""
                ).casefold().endswith(".services.ai.azure.com"):
                    raise ControlPlaneConflictError(
                        "Foundry Claude requires a services.ai.azure.com Project connection"
                    )
                binding = GatewayModelBinding.model_validate(
                    {
                        **binding.model_dump(mode="python"),
                        "api_format": ApiFormat.ANTHROPIC_MESSAGES,
                        "backend_url": f"{endpoint.scheme}://{endpoint.netloc}",
                        "backend_path": "/anthropic/v1/messages",
                        "runtime_config": {
                            **config,
                            "anthropic_version": "2023-06-01",
                        },
                    }
                )
            bindings.append(binding)
        formats = {
            binding.model.model_key.casefold(): binding.api_format
            for binding in bindings
        }
        discovery = [
            item.model_copy(update={"api_format": formats.get(item.id.casefold(), item.api_format)})
            for item in spec.discovery_models
        ]
        return spec.model_copy(
            update={"bindings": bindings, "discovery_models": discovery}
        )

    @staticmethod
    def _find(rows: Sequence[dict[str, Any]], item_id: UUID) -> dict[str, Any] | None:
        return next((row for row in rows if row["id"] == item_id), None)

    def _resolve_provider(
        self,
        write: GatewayPublicationCreate,
        providers: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        target = write.provider
        if target.existing_id is not None:
            provider = self._find(providers, target.existing_id)
            if provider is None or not provider["enabled"]:
                raise ControlPlaneConflictError("The selected provider is unavailable")
            return provider
        assert target.template is not None
        template = target.template
        provider_name = {
            "amazon_bedrock": "Amazon Bedrock",
            "microsoft_foundry": "Microsoft Foundry",
            "openai_compatible": "OpenAI-compatible",
        }[template]
        if any(
            str(row["name"]).casefold() == provider_name.casefold()
            for row in providers
        ):
            raise ControlPlaneConflictError(
                f"A {provider_name} provider already exists; select it instead"
            )
        if template == "microsoft_foundry":
            return {
                "id": None,
                "name": provider_name,
                "provider_kind": "microsoft_foundry",
                "brand_key": BrandKey.MICROSOFT_FOUNDRY,
                "config": {"hosting_platform": "microsoft_foundry"},
            }
        if template == "openai_compatible":
            return {
                "id": None,
                "name": provider_name,
                "provider_kind": "openai_compatible",
                "brand_key": BrandKey.GENERIC,
                "config": {"hosting_platform": "openai_compatible"},
            }
        return {
            "id": None,
            "name": provider_name,
            "provider_kind": "anthropic",
            "brand_key": BrandKey.AMAZON_BEDROCK,
            "config": {"hosting_platform": "amazon_bedrock"},
        }

    def _resolve_runtime(
        self,
        write: GatewayPublicationCreate,
        provider: Mapping[str, Any],
        runtimes: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        target = write.runtime
        if target.existing_id is not None:
            runtime = self._find(runtimes, target.existing_id)
            if runtime is None or not runtime["enabled"]:
                raise ControlPlaneConflictError("The selected runtime is unavailable")
            if runtime["provider_id"] != provider["id"]:
                raise ControlPlaneConflictError(
                    "The selected runtime does not belong to the provider"
                )
            if runtime.get("gateway_profile_id") != write.gateway_profile_id:
                raise ControlPlaneConflictError(
                    "The selected runtime does not use the selected gateway"
                )
            config = dict(runtime.get("config") or {})
            auth_strategy = AuthStrategy(
                config.get("auth_strategy", AuthStrategy.NONE)
            )
            credential_pending = (
                config.get("credential_provisioned") is False
                and auth_strategy
                in {
                    AuthStrategy.NAMED_VALUE_BEARER,
                    AuthStrategy.NAMED_VALUE_API_KEY,
                }
            )
            if credential_pending and target.api_key is None:
                raise ControlPlaneConflictError(
                    "The selected connection requires a one-time API key for its first model"
                )
            if not credential_pending and target.api_key is not None:
                raise ControlPlaneConflictError(
                    "The selected connection already has a provisioned credential"
                )
            if credential_pending:
                config["credential_provisioned"] = True
            runtime_brand = BrandKey(
                runtime.get("brand_key", provider["brand_key"])
            )
            api_format = (
                ApiFormat.ANTHROPIC_MESSAGES
                if config.get("api_format") == "anthropic_messages"
                or runtime_brand in {
                    BrandKey.AMAZON_BEDROCK,
                    BrandKey.AZURE_DATABRICKS,
                }
                else ApiFormat.OPENAI_CHAT
            )
            routing_managed = bool(config.get("control_plane_managed", False))
            backend_path = config.get("backend_path")
            if not backend_path and routing_managed:
                if runtime_brand is BrandKey.MICROSOFT_FOUNDRY:
                    backend_path = "/openai/v1/chat/completions"
                elif runtime_brand is BrandKey.AMAZON_BEDROCK:
                    backend_path = "/model/{upstream_model_id}/invoke"
                else:
                    raise ControlPlaneConflictError(
                        "The selected managed runtime is missing its provider backend path"
                    )
            if not backend_path:
                backend_path = config.get("path", "/v1/messages")
            return {
                "id": runtime["id"],
                "routing_managed": routing_managed,
                "name": runtime["name"],
                "runtime_kind": runtime["runtime_kind"],
                "brand_key": runtime_brand,
                "api_format": api_format,
                "backend_url": config.get("backend_url"),
                "backend_path": backend_path,
                "auth_strategy": auth_strategy,
                "named_value_name": config.get("named_value_name"),
                "key_vault_secret_id": config.get("key_vault_secret_id"),
                "managed_identity_resource": config.get("managed_identity_resource"),
                "streaming_mode": config.get("streaming_mode", StreamingMode.NATIVE),
                "config": config,
            }
        brand = BrandKey(provider["brand_key"])
        if provider["provider_kind"] == "openai_compatible":
            if target.openai_base_url is None or target.api_key is None:
                raise ControlPlaneConflictError(
                    "OpenAI-compatible publication requires a Base URL and API key"
                )
            try:
                base_url, backend_url, backend_path = openai_compatible_endpoint_values(
                    str(target.openai_base_url)
                )
            except ValueError as error:
                raise ControlPlaneConflictError(str(error)) from error
            if any(
                row.get("gateway_profile_id") == write.gateway_profile_id
                and str((row.get("config") or {}).get("base_url", "")).rstrip("/").casefold()
                == base_url.casefold()
                for row in runtimes
            ):
                raise ControlPlaneConflictError(
                    "An OpenAI-compatible runtime already exists for this gateway and Base URL"
                )
            scope = f"{write.gateway_profile_id}:{base_url.casefold()}"
            named_value = (
                "turnstile-openai-" + hashlib.sha256(scope.encode("utf-8")).hexdigest()[:16]
            )
            return {
                "id": None,
                "routing_managed": True,
                "name": openai_compatible_runtime_name(base_url),
                "runtime_kind": "openai_compatible",
                "brand_key": brand,
                "api_format": ApiFormat.OPENAI_CHAT,
                "backend_url": backend_url,
                "backend_path": backend_path,
                "auth_strategy": AuthStrategy.NAMED_VALUE_BEARER,
                "named_value_name": named_value,
                "key_vault_secret_id": None,
                "managed_identity_resource": None,
                "streaming_mode": StreamingMode.NATIVE,
                "config": {
                    "base_url": base_url,
                    "credential_kind": "api_key",
                    "max_tokens_field": "max_tokens",
                    "supports_temperature": True,
                },
            }
        if brand is BrandKey.AMAZON_BEDROCK:
            assert target.bedrock_runtime_url is not None
            assert target.api_key is not None
            assert write.model.model_key is not None
            endpoint = urlsplit(str(target.bedrock_runtime_url))
            host = (endpoint.hostname or "").casefold()
            suffix = ".amazonaws.com"
            prefix = "bedrock-runtime."
            region = (
                host.removeprefix(prefix).removesuffix(suffix)
                if host.startswith(prefix) and host.endswith(suffix)
                else ""
            )
            if endpoint.path not in {"", "/"} or endpoint.query or endpoint.fragment:
                raise ControlPlaneConflictError(
                    "The Bedrock Runtime URL must contain only the regional origin"
                )
            backend_url = f"{endpoint.scheme}://{endpoint.netloc}"
            duplicate_runtime = next(
                (
                    row
                    for row in runtimes
                    if row.get("provider_id") == provider.get("id")
                    and row.get("gateway_profile_id") == write.gateway_profile_id
                    and str((row.get("config") or {}).get("backend_url", "")).rstrip("/").casefold()
                    == backend_url.rstrip("/").casefold()
                ),
                None,
            )
            if duplicate_runtime is not None:
                raise ControlPlaneConflictError(
                    "A Bedrock runtime already exists for this gateway and Region; "
                    "select it or rotate its credential"
                )
            runtime_name = f"Amazon Bedrock Claude ({region}) via APIM"
            credential_scope = (
                f"{write.gateway_profile_id}:{backend_url}:"
                f"{write.model.model_key.casefold()}"
            )
            generated_named_value = (
                "finops-bedrock-"
                + hashlib.sha256(credential_scope.encode("utf-8")).hexdigest()[:16]
            )
            return {
                "id": None,
                "routing_managed": True,
                "name": runtime_name,
                "runtime_kind": "openai_compatible",
                "brand_key": BrandKey.AMAZON_BEDROCK,
                "api_format": ApiFormat.ANTHROPIC_MESSAGES,
                "backend_url": backend_url,
                "backend_path": "/model/{upstream_model_id}/invoke",
                "auth_strategy": AuthStrategy.NAMED_VALUE_BEARER,
                "named_value_name": generated_named_value,
                "key_vault_secret_id": None,
                "managed_identity_resource": None,
                "streaming_mode": StreamingMode.BUFFERED,
                "config": {"region": region},
            }
        if brand is BrandKey.MICROSOFT_FOUNDRY:
            assert target.foundry_project_endpoint is not None
            uses_api_key = target.foundry_inference_endpoint is not None
            if not uses_api_key and not self._apim_principal_id:
                raise ControlPlaneConflictError(
                    "APIM managed identity is not configured for Foundry onboarding"
                )
            endpoint = urlsplit(str(target.foundry_project_endpoint))
            host = (endpoint.hostname or "").casefold()
            suffix = ".services.ai.azure.com"
            account = host.removesuffix(suffix) if host.endswith(suffix) else ""
            path_match = re.fullmatch(
                r"/api/projects/(?P<project>[A-Za-z0-9._-]+)/?",
                endpoint.path,
            )
            if (
                endpoint.scheme != "https"
                or not account
                or path_match is None
                or endpoint.query
                or endpoint.fragment
                or endpoint.username is not None
            ):
                raise ControlPlaneConflictError(
                    "The Foundry project endpoint must use "
                    "https://<account>.services.ai.azure.com/api/projects/<project>"
                )
            project = unquote(path_match.group("project"))
            project_endpoint = (
                f"{endpoint.scheme}://{endpoint.netloc}/api/projects/{project}"
            )
            backend_url = project_endpoint
            auth_strategy = AuthStrategy.MANAGED_IDENTITY
            named_value_name = None
            managed_identity_resource: str | None = FOUNDRY_INFERENCE_RESOURCE
            runtime_config: dict[str, object] = {
                "project_endpoint": project_endpoint,
                "max_tokens_field": "max_completion_tokens",
                "supports_temperature": False,
            }
            if uses_api_key:
                assert target.foundry_inference_endpoint is not None
                assert target.api_key is not None
                inference = urlsplit(str(target.foundry_inference_endpoint))
                inference_host = (inference.hostname or "").casefold()
                valid_inference_host = inference_host in {
                    f"{account}.openai.azure.com",
                    f"{account}.services.ai.azure.com",
                }
                if (
                    inference.scheme != "https"
                    or not valid_inference_host
                    or inference.path.rstrip("/") != "/openai/v1"
                    or inference.query
                    or inference.fragment
                    or inference.username is not None
                ):
                    raise ControlPlaneConflictError(
                        "The Foundry inference endpoint must use the same account at "
                        "https://<account>.openai.azure.com/openai/v1 or "
                        "https://<account>.services.ai.azure.com/openai/v1"
                    )
                backend_url = f"{inference.scheme}://{inference.netloc}"
                credential_scope = (
                    f"{write.gateway_profile_id}:{project_endpoint}:{backend_url}"
                )
                named_value_name = (
                    "finops-foundry-key-"
                    + hashlib.sha256(credential_scope.encode("utf-8")).hexdigest()[:16]
                )
                auth_strategy = AuthStrategy.NAMED_VALUE_API_KEY
                managed_identity_resource = None
                runtime_config.update(
                    inference_endpoint=str(target.foundry_inference_endpoint).rstrip("/"),
                    credential_kind="api_key",
                )
            else:
                runtime_config["authorization"] = {
                    "kind": "azure_rbac",
                    "principal_id": self._apim_principal_id,
                    "resource_endpoint": f"{endpoint.scheme}://{endpoint.netloc}",
                        "role_id": FOUNDRY_INFERENCE_ROLE_ID,
                    "role_name": "Cognitive Services User",
                }
            duplicate_runtime = next(
                (
                    row
                    for row in runtimes
                    if row.get("gateway_profile_id") == write.gateway_profile_id
                    and str((row.get("config") or {}).get("project_endpoint", ""))
                    .rstrip("/")
                    .casefold()
                    == project_endpoint.casefold()
                ),
                None,
            )
            if duplicate_runtime is not None:
                raise ControlPlaneConflictError(
                    "A Foundry runtime already exists for this gateway and project"
                )
            return {
                "id": None,
                "routing_managed": True,
                "name": foundry_runtime_name(account, project),
                "runtime_kind": "foundry",
                "brand_key": BrandKey.MICROSOFT_FOUNDRY,
                "api_format": ApiFormat.OPENAI_CHAT,
                "backend_url": backend_url,
                "backend_path": "/openai/v1/chat/completions",
                "auth_strategy": auth_strategy,
                "named_value_name": named_value_name,
                "key_vault_secret_id": None,
                "managed_identity_resource": managed_identity_resource,
                "streaming_mode": StreamingMode.NATIVE,
                "config": runtime_config,
            }
        raise ControlPlaneConflictError(
            "New runtime publication requires a supported provider template"
        )

    @staticmethod
    def _validate_model(
        model: ModelTarget, models: Sequence[dict[str, Any]]
    ) -> None:
        alias = model.model_key.casefold()
        if any(str(row["model_key"]).casefold() == alias for row in models):
            raise ControlPlaneConflictError("The model alias is already active")

    @staticmethod
    def _release_model(
        model: ModelCreateTarget,
        provider: Mapping[str, Any],
        runtime: Mapping[str, Any],
    ) -> ModelTarget:
        brand = BrandKey(provider["brand_key"])
        if brand is BrandKey.MICROSOFT_FOUNDRY:
            if model.deployment_name is None:
                raise ControlPlaneConflictError(
                    "Microsoft Foundry requires an existing deployment name"
                )
            deployment = model.deployment_name.strip()
            config = dict(runtime.get("config") or {})
            connection = str(
                config.get("project_endpoint") or runtime.get("backend_url") or ""
            ).rstrip("/").casefold()
            if not connection:
                raise ControlPlaneConflictError(
                    "The selected runtime is not a managed Foundry Project connection"
                )
            slug = re.sub(r"[^a-z0-9._:-]+", "-", deployment.casefold()).strip("-")
            if not slug:
                raise ControlPlaneConflictError("The Foundry deployment name is invalid")
            suffix = hashlib.sha256(connection.encode("utf-8")).hexdigest()[:8]
            model_key = f"{slug[:220]}-foundry-{suffix}"
            identity = f"{model_key} {deployment}".casefold()
            family = (
                ModelFamilyKey.CLAUDE
                if "claude" in identity
                else ModelFamilyKey.OPENAI
                if "gpt" in identity or "openai" in identity
                else ModelFamilyKey.GENERIC
            )
            derived_capabilities: list[ModelCapability] = (
                ["chat", "tools", "vision", "reasoning", "streaming"]
                if family is ModelFamilyKey.CLAUDE
                else ["chat", "tools", "streaming"]
                if family is ModelFamilyKey.OPENAI
                else ["chat", "streaming"]
            )
            return ModelTarget(
                model_key=model_key,
                display_name=f"{deployment} · Microsoft Foundry",
                upstream_model_id=deployment,
                family_key=family,
                capabilities=derived_capabilities,
                context_window=model.context_window,
                input_cost_per_million=model.input_cost_per_million,
                output_cost_per_million=model.output_cost_per_million,
                cached_cost_per_million=model.cached_cost_per_million,
                cache_write_cost_per_million=model.cache_write_cost_per_million,
                allowed_roles=["owner", "admin", "member"],
                assignment_required=True,
            )
        assert model.model_key is not None
        assert model.display_name is not None
        assert model.upstream_model_id is not None
        identity = f"{model.model_key} {model.display_name} {model.upstream_model_id}".casefold()
        if brand is BrandKey.AMAZON_BEDROCK and "claude" not in identity:
            raise ControlPlaneConflictError(
                "Automated Anthropic publication requires a Claude model"
            )
        family = (
            ModelFamilyKey.CLAUDE
            if "claude" in identity
            else ModelFamilyKey.OPENAI
            if "gpt" in identity or "openai" in identity
            else ModelFamilyKey.GENERIC
        )
        capabilities: list[ModelCapability] = (
            ["chat", "tools", "vision", "reasoning", "streaming"]
            if runtime["api_format"] == ApiFormat.ANTHROPIC_MESSAGES
            else ["chat", "streaming"]
        )
        return ModelTarget(
            **model.model_dump(exclude={"deployment_name"}),
            family_key=family,
            capabilities=capabilities,
            allowed_roles=["owner", "admin", "member"],
            assignment_required=True,
        )

    @staticmethod
    def _model_binding_runtime(
        provider: Mapping[str, Any],
        runtime: Mapping[str, Any],
        model: ModelTarget,
    ) -> dict[str, Any]:
        result = dict(runtime)
        if (
            BrandKey(provider["brand_key"]) is not BrandKey.MICROSOFT_FOUNDRY
            or model.family_key is not ModelFamilyKey.CLAUDE
        ):
            return result
        config = dict(runtime.get("config") or {})
        endpoint = urlsplit(
            str(config.get("project_endpoint") or runtime.get("backend_url") or "")
        )
        if endpoint.scheme != "https" or not (
            endpoint.hostname or ""
        ).casefold().endswith(".services.ai.azure.com"):
            raise ControlPlaneConflictError(
                "Foundry Claude requires a services.ai.azure.com Project connection"
            )
        result.update(
            api_format=ApiFormat.ANTHROPIC_MESSAGES,
            backend_url=f"{endpoint.scheme}://{endpoint.netloc}",
            backend_path="/anthropic/v1/messages",
            config={**config, "anthropic_version": "2023-06-01"},
        )
        return result

    @classmethod
    def _validate_connection(
        cls, provider: Mapping[str, Any], runtime: Mapping[str, Any]
    ) -> None:
        brand = BrandKey(provider["brand_key"])
        if runtime.get("id") is not None and brand is not BrandKey.MICROSOFT_FOUNDRY:
            return
        url = str(runtime["backend_url"])
        host = (urlsplit(url).hostname or "").casefold()
        if provider["provider_kind"] == "openai_compatible":
            if urlsplit(url).scheme != "https" or not host:
                raise ControlPlaneConflictError("OpenAI-compatible backends require HTTPS")
            if runtime["api_format"] != ApiFormat.OPENAI_CHAT:
                raise ControlPlaneConflictError(
                    "OpenAI-compatible runtimes require the Chat Completions format"
                )
            if runtime["auth_strategy"] != AuthStrategy.NAMED_VALUE_BEARER:
                raise ControlPlaneConflictError(
                    "OpenAI-compatible runtimes require bearer API-key authentication"
                )
            if not runtime["routing_managed"] or not runtime.get("backend_path"):
                raise ControlPlaneConflictError(
                    "The selected runtime is not a managed OpenAI-compatible connection"
                )
            if runtime["streaming_mode"] != StreamingMode.NATIVE:
                raise ControlPlaneConflictError(
                    "OpenAI-compatible runtimes require native streaming"
                )
            return
        if brand is BrandKey.AMAZON_BEDROCK:
            if runtime["api_format"] != ApiFormat.ANTHROPIC_MESSAGES:
                raise ControlPlaneConflictError(
                    "Amazon Bedrock Claude requires the Anthropic Messages format"
                )
            if provider["provider_kind"] != "anthropic":
                raise ControlPlaneConflictError(
                    "Amazon Bedrock Claude must use the Anthropic Messages protocol"
                )
            if not host.startswith("bedrock-runtime.") or not host.endswith(".amazonaws.com"):
                raise ControlPlaneConflictError(
                    "Amazon Bedrock backends must use a regional "
                    "bedrock-runtime.amazonaws.com endpoint"
                )
            if runtime["auth_strategy"] != AuthStrategy.NAMED_VALUE_BEARER:
                raise ControlPlaneConflictError(
                    "Amazon Bedrock requires a release-scoped bearer named value"
                )
            # This is an AWS Bedrock short/long-term API key, whose verified wire shape is
            # `Authorization: Bearer`. It is not an IAM access-key pair and must not be
            # silently converted to SigV4 without a separate credential model.
            if runtime["streaming_mode"] != StreamingMode.BUFFERED:
                raise ControlPlaneConflictError(
                    "Amazon Bedrock requires buffered Anthropic streaming in APIM"
                )
            return
        if brand is BrandKey.MICROSOFT_FOUNDRY:
            if provider["provider_kind"] != "microsoft_foundry":
                raise ControlPlaneConflictError(
                    "Microsoft Foundry must use the Foundry provider kind"
                )
            config = dict(runtime.get("config") or {})
            auth_strategy = AuthStrategy(runtime["auth_strategy"])
            if (
                auth_strategy is AuthStrategy.MANAGED_IDENTITY
                and not host.endswith(".services.ai.azure.com")
            ):
                raise ControlPlaneConflictError(
                    "Foundry project endpoints must use services.ai.azure.com"
                )
            if auth_strategy not in {
                AuthStrategy.MANAGED_IDENTITY,
                AuthStrategy.NAMED_VALUE_API_KEY,
            }:
                raise ControlPlaneConflictError(
                    "Microsoft Foundry requires a supported authentication strategy"
                )
            if (
                auth_strategy is AuthStrategy.MANAGED_IDENTITY
                and runtime["managed_identity_resource"] != FOUNDRY_INFERENCE_RESOURCE
            ):
                raise ControlPlaneConflictError(
                    "Microsoft Foundry requires the ai.azure.com token audience"
                )
            if auth_strategy is AuthStrategy.NAMED_VALUE_API_KEY and (
                not runtime.get("named_value_name")
                or not config.get("inference_endpoint")
            ):
                raise ControlPlaneConflictError(
                    "Foundry API-key authentication requires a secret named value"
                )
            if runtime["api_format"] != ApiFormat.OPENAI_CHAT:
                raise ControlPlaneConflictError(
                    "Microsoft Foundry onboarding requires the OpenAI chat format"
                )
            if not runtime["routing_managed"] or not config.get("project_endpoint"):
                raise ControlPlaneConflictError(
                    "The selected runtime is not a managed Foundry Project connection"
                )
            return
        raise ControlPlaneConflictError(
            "New runtime publication requires a supported provider template"
        )

    @staticmethod
    def _existing_discovery(
        gateway_profile_id: UUID, registry: Mapping[str, Sequence[dict[str, Any]]]
    ) -> list[DiscoveryModel]:
        runtimes = {
            row["id"]: row
            for row in registry["runtimes"]
            if row.get("gateway_profile_id") == gateway_profile_id and row["enabled"]
        }
        result: list[DiscoveryModel] = []
        for model in registry["models"]:
            runtime = runtimes.get(model["runtime_id"])
            if runtime is None or not model["enabled"]:
                continue
            result.append(
                DiscoveryModel(
                    id=model["model_key"],
                    display_name=model["display_name"],
                    api_format=GatewayControlPlaneService._model_api_format(
                        model, runtime
                    ),
                )
            )
        return result

    @staticmethod
    def _model_api_format(
        model: Mapping[str, Any], runtime: Mapping[str, Any]
    ) -> ApiFormat:
        if (
            runtime.get("brand_key") in {
                BrandKey.MICROSOFT_FOUNDRY,
                BrandKey.MICROSOFT_FOUNDRY.value,
            }
            and model.get("family_key") in {
                ModelFamilyKey.CLAUDE,
                ModelFamilyKey.CLAUDE.value,
            }
        ):
            return ApiFormat.ANTHROPIC_MESSAGES
        return GatewayControlPlaneService._runtime_api_format(runtime)

    @staticmethod
    def _runtime_api_format(runtime: Mapping[str, Any]) -> ApiFormat:
        config = dict(runtime.get("config") or {})
        brand = runtime.get("brand_key")
        if config.get("api_format") == ApiFormat.ANTHROPIC_MESSAGES or brand in {
            BrandKey.AMAZON_BEDROCK,
            BrandKey.ANTHROPIC,
            BrandKey.AZURE_DATABRICKS,
            "amazon_bedrock",
            "anthropic",
            "azure_databricks",
        }:
            return ApiFormat.ANTHROPIC_MESSAGES
        return ApiFormat.OPENAI_CHAT