from __future__ import annotations

import inspect

import pytest

from turnstile_core.config import Settings
from turnstile_core.persistence.factory import create_repository
from turnstile_core.persistence.in_memory import InMemoryRepository
from turnstile_core.persistence.repository import PostgreSqlOpsDbProxy, QueryRepository


def test_in_memory_repository_preserves_the_public_contract() -> None:
    assert InMemoryRepository.__module__ == "turnstile_core.persistence.in_memory"
    assert not inspect.isabstract(InMemoryRepository)
    assert all(
        callable(getattr(InMemoryRepository, method_name))
        for method_name in QueryRepository.__abstractmethods__
    )


def test_postgresql_is_the_default_and_requires_database_url() -> None:
    settings = Settings(data_backend="postgresql", database_url=None)

    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        create_repository(settings)


def test_demo_repository_requires_explicit_non_production_mode() -> None:
    repository = create_repository(
        Settings(data_backend="demo", database_url=None, production=False)
    )

    assert isinstance(repository, InMemoryRepository)


def test_production_rejects_demo_repository() -> None:
    settings = Settings(data_backend="demo", database_url=None, production=True)

    with pytest.raises(RuntimeError, match="forbidden"):
        create_repository(settings)


def test_postgresql_repository_is_selected_when_configured() -> None:
    repository = create_repository(
        Settings(
            data_backend="postgresql",
            database_url="postgresql://example.invalid/finops",
        )
    )

    assert isinstance(repository, PostgreSqlOpsDbProxy)