"""Create or update a sign-in account.

    uv run python -m backend.accounts alice@contoso.com --role owner

A command rather than an endpoint, deliberately. An HTTP route that mints accounts is a
route that has to decide who may call it before any account exists to authorise -- the
bootstrap problem every "create the first admin" feature runs into, usually solved with a
setup token that then lives forever. Running this needs shell access to the deployment,
which is a stronger check than anything the API could offer and requires no new secret.

The password is read from a prompt, never an argument: `ps` shows one and shell history
keeps it.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from turnstile_core.config import get_settings
from turnstile_core.persistence.auth_store import AuthStore

from .services.auth_service import hash_password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create or update a Turnstile account.")
    parser.add_argument("email")
    # Optional, and it stays that way. A required name with no authoritative source is a
    # field that gets filled with a guess -- which is how this deployment's first account
    # came to display a person's name that nobody had supplied. Omitted means the sidebar
    # shows the email, which is what the person typed to get in.
    parser.add_argument("--name", default=None, help="Optional display name.")
    parser.add_argument("--role", choices=("owner", "member"), default="member")
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.database_url:
        print("DATABASE_URL is required.", file=sys.stderr)
        return 2

    email = args.email.strip().lower()
    if "@" not in email:
        print("Email must contain '@'.", file=sys.stderr)
        return 2

    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat: "):
        print("Passwords do not match.", file=sys.stderr)
        return 2
    # Long enough that scrypt's cost is the attacker's problem rather than the alphabet's.
    # Not a complexity rule: those push people towards `Passw0rd!` and a length floor buys
    # more entropy than a symbol requirement does.
    if len(password) < 12:
        print("Password must be at least 12 characters.", file=sys.stderr)
        return 2

    store = AuthStore(settings.database_url)
    try:
        user = store.create_password_user(email, args.name, hash_password(password), args.role)
    finally:
        store.close()
    print(f"{user['email']}  role={user['role']}  id={user['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
