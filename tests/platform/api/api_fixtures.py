from __future__ import annotations

from collections.abc import Iterator

import pytest

from backend.api import app
from backend.http.dependencies import get_repository
from backend.http.session import get_auth_store
from tests.platform.api.api_support import OWNER_SESSION, StubAuthStore, client
from turnstile_core.persistence.in_memory import InMemoryRepository


@pytest.fixture(autouse=True)
def explicit_demo_repository() -> Iterator[None]:
    repository = InMemoryRepository()
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_auth_store] = StubAuthStore
    client.cookies.set("turnstile_session", OWNER_SESSION)
    yield
    client.cookies.clear()
    app.dependency_overrides.pop(get_repository, None)
    app.dependency_overrides.pop(get_auth_store, None)