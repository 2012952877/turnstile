from __future__ import annotations

from pydantic import SecretStr

from backend.bootstrap import bootstrap_initial_owner
from backend.services.auth_service import hash_password, verify_password
from turnstile_core.config import Settings


class CapturingInitialOwnerStore:
    def __init__(self, *, created: bool = True) -> None:
        self.created = created
        self.calls: list[tuple[str, str]] = []

    def create_initial_owner(self, email: str, password_hash: str) -> dict[str, object] | None:
        self.calls.append((email, password_hash))
        return {"role": "owner"} if self.created else None


def test_bootstrap_creates_owner_with_configured_hash() -> None:
    password_hash = hash_password("a-long-bootstrap-password")
    settings = Settings(
        bootstrap_owner_email=" Owner@Contoso.com ",
        bootstrap_owner_password_hash=SecretStr(password_hash),
    )
    store = CapturingInitialOwnerStore()

    assert bootstrap_initial_owner(store, settings)
    assert store.calls[0][0] == "owner@contoso.com"
    assert verify_password("a-long-bootstrap-password", store.calls[0][1])


def test_bootstrap_is_a_noop_when_users_already_exist() -> None:
    settings = Settings(
        bootstrap_owner_email="owner@contoso.com",
        bootstrap_owner_password_hash=SecretStr(hash_password("a-long-bootstrap-password")),
    )

    assert not bootstrap_initial_owner(CapturingInitialOwnerStore(created=False), settings)


def test_bootstrap_configuration_must_be_paired() -> None:
    try:
        Settings(bootstrap_owner_email="owner@contoso.com")
    except ValueError as error:
        assert "must be configured together" in str(error)
    else:
        raise AssertionError("one-sided bootstrap configuration was accepted")