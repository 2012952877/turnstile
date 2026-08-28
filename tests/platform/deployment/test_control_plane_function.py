from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from backend.security import CredentialCipher
from functions.control_plane import function_app


def test_disabled_publication_timer_does_not_build_dependencies(monkeypatch: Any) -> None:
    settings = SimpleNamespace(
        control_plane_enabled=True,
        gateway_publication_worker_enabled=False,
    )
    monkeypatch.setattr(function_app, "get_settings", lambda: settings)

    def reject_repository(_: object) -> None:
        raise AssertionError("disabled publication timer created a repository")

    monkeypatch.setattr(function_app, "create_repository", reject_repository)

    function_app.publish_gateway_changes(None)


def test_release_timer_never_constructs_publication_worker(monkeypatch: Any) -> None:
    settings = SimpleNamespace(
        control_plane_enabled=True,
        gateway_release_worker_enabled=True,
        gateway_release_retention_count=20,
        gateway_release_retention_days=180,
        gateway_failed_release_retention_days=30,
        gateway_release_protected_labels=["milestone", "rollback"],
        gateway_application_default_monthly_token_limit=100_000,
        gateway_application_default_tokens_per_minute=100_000,
        control_plane_lease_seconds=180,
        control_plane_max_attempts=30,
    )
    repository = object()
    publisher = object()
    cipher = object()
    calls: list[tuple[str, int, int]] = []

    class ReleaseWorker:
        def __init__(
            self,
            actual_repository: object,
            actual_publisher: object,
            _: object,
            **kwargs: object,
        ):
            assert actual_repository is repository
            assert actual_publisher is publisher
            assert kwargs == {
                "application_default_token_limit": 100_000,
                "application_default_tokens_per_minute": 100_000,
                "cipher": cipher,
            }

        def run_once(
            self, worker_id: str, lease_seconds: int, max_attempts: int
        ) -> None:
            calls.append((worker_id, lease_seconds, max_attempts))

    class PublicationWorker:
        def __init__(self, *_: object, **__: object) -> None:
            raise AssertionError("release timer constructed publication worker")

    monkeypatch.setattr(function_app, "get_settings", lambda: settings)
    monkeypatch.setattr(function_app, "create_repository", lambda _: repository)
    monkeypatch.setattr(function_app, "AzureApimPublisherClient", lambda _: publisher)
    monkeypatch.setattr(
        CredentialCipher, "from_settings", lambda _: cipher
    )
    monkeypatch.setattr(function_app, "GatewayReleaseOperationWorker", ReleaseWorker)
    monkeypatch.setattr(function_app, "GatewayPublicationWorker", PublicationWorker)
    monkeypatch.setenv("WEBSITE_INSTANCE_ID", "release-worker-instance")

    function_app.process_gateway_release_operations(None)

    assert calls == [("release-worker-instance", 180, 30)]