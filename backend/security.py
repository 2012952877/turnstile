from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import Settings


class CredentialError(ValueError):
    pass


class CredentialCipher:
    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    @classmethod
    def from_settings(cls, settings: Settings) -> CredentialCipher:
        configured = settings.credential_encryption_key
        if configured is not None:
            return cls(configured.get_secret_value().encode("ascii"))
        if settings.production:
            raise CredentialError("CREDENTIAL_ENCRYPTION_KEY is required in production")
        return cls(cls._load_or_create_local_key(settings.credential_key_file))

    @staticmethod
    def _load_or_create_local_key(path: Path) -> bytes:
        if path.exists():
            return path.read_bytes().strip()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        key = Fernet.generate_key()
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(key)
        return key

    def encrypt(self, value: str) -> bytes:
        return self._fernet.encrypt(value.encode("utf-8"))

    def decrypt(self, value: bytes | memoryview | None) -> str | None:
        if value is None:
            return None
        try:
            return self._fernet.decrypt(bytes(value)).decode("utf-8")
        except InvalidToken as error:
            raise CredentialError(
                "Stored credential cannot be decrypted with the active key"
            ) from error


def credential_hint(value: str) -> str:
    if len(value) <= 4:
        return "configured"
    return f"...{value[-4:]}"
