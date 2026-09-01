"""Create the first password Owner during an empty-database deployment."""

from __future__ import annotations

import sys
from typing import Protocol

from turnstile_core.config import Settings, get_settings
from turnstile_core.persistence.auth_store import AuthStore


class InitialOwnerStore(Protocol):
    def create_initial_owner(self, email: str, password_hash: str) -> dict[str, object] | None: ...


def bootstrap_initial_owner(store: InitialOwnerStore, settings: Settings) -> bool:
    email = settings.bootstrap_owner_email.strip().lower()
    configured_hash = settings.bootstrap_owner_password_hash
    if not email or configured_hash is None:
        return False
    return store.create_initial_owner(email, configured_hash.get_secret_value()) is not None


def main() -> int:
    settings = get_settings()
    if not settings.bootstrap_owner_email:
        return 0
    if not settings.database_url:
        print("DATABASE_URL is required to bootstrap the initial Owner.", file=sys.stderr)
        return 2
    store = AuthStore(settings.database_url)
    try:
        created = bootstrap_initial_owner(store, settings)
    finally:
        store.close()
    print("Initial Owner created." if created else "Initial Owner bootstrap skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())