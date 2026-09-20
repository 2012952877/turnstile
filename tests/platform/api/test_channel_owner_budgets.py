"""The chain an install with per-person subscription keys actually walks.

Subscription in the inventory -> owner and department recorded on it -> that person
allocatable in the budget screen. The middle step existed and the last one did not: the
budget API validates a user scope against the governance directory, and the directory only
ever admitted people who had called the gateway or held a console Owner account.

That excluded the entire population this is for -- several hundred people who each hold a
key, most of whom have not called anything yet.
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.api import app, application_access_service
from backend.http.dependencies import get_repository
from backend.http.session import require_authenticated_session
from tests.backend.model_platform.control_plane_support import APIM_ID
from tests.platform.api.api_support import client
from turnstile_core.domain.application_access import (
    GatewayApplicationDiscovery,
    GatewayApplicationDiscoveryItem,
)
from turnstile_core.persistence.in_memory import InMemoryRepository
from turnstile_core.services.application_access import ApplicationAccessService

pytest_plugins = ("tests.platform.api.api_fixtures",)

PERSON = "a.aiginin@insilicomedicine.com"
OWNERSHIP = "/api/v1/application-access/applications/{0}/ownership"
PEOPLE = "/api/v1/budgets/users?period={0}&department_id={1}&limit=100"
PERIOD = datetime.now(UTC).strftime("%Y-%m")


def _seed(repository: InMemoryRepository) -> ApplicationAccessService:
    service = ApplicationAccessService(repository, sync_available=True)
    service.sync_discovery(
        GatewayApplicationDiscovery(
            gateway_profile_id=APIM_ID,
            discovered_at=datetime(2026, 9, 20, 2, tzinfo=UTC),
            items=[
                GatewayApplicationDiscoveryItem(
                    apim_subscription_id="sub-a-aiginin-insilicomedicine-com",
                    display_name=f"{PERSON} - IT Databricks Claude API",
                    state="active",
                    scope_type="product",
                    scope_id="finops-applications",
                    scope_exists=True,
                    application_type="service",
                    system_managed=False,
                )
            ],
        ),
        "application-sync-worker",
    )
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[application_access_service] = lambda: service
    return service


def _uninstall() -> None:
    app.dependency_overrides.pop(application_access_service, None)
    app.dependency_overrides.pop(get_repository, None)
    app.dependency_overrides.pop(require_authenticated_session, None)


def _people(department: str) -> list[str]:
    body = client.get(PEOPLE.format(PERIOD, department)).json()
    return [item.get("scope_id") or item.get("scope_name") for item in body.get("items", [])]


def test_recording_an_owner_makes_that_person_allocatable() -> None:
    repository = InMemoryRepository()
    _seed(repository)
    application_id = repository.gateway_applications[0]["id"]
    try:
        assert PERSON not in _people("department-platform")

        assert client.put(
            OWNERSHIP.format(application_id),
            json={"owner_id": PERSON, "department_id": "department-platform"},
        ).status_code == 200

        assert PERSON in _people("department-platform")

        # Allocatable, not merely listed: the budget API validates the scope against the
        # same directory, so appearing there is the whole of what was missing. Allocation is
        # top-down -- a user budget without a department budget above it is a 409 -- so the
        # parents come first, which is the order the screen walks anyway.
        for scope_type, scope_id, limit in (
            ("organization", "org-contoso-global", 10_000_000),
            ("department", "department-platform", 1_000_000),
            ("user", PERSON, 250_000),
        ):
            response = client.put(
                f"/api/v1/budgets/{scope_type}/{scope_id}?period={PERIOD}",
                json={"token_limit": limit, "warning_threshold_percent": 80},
            )
            assert response.status_code == 200, (scope_id, response.text)
    finally:
        _uninstall()


def test_an_owner_without_a_department_is_not_listed() -> None:
    """A person with nowhere to hang is worse on the page than absent.

    The hierarchy is organization -> department -> user. Listing someone with no department
    would offer a name that no allocation can be made against.
    """
    repository = InMemoryRepository()
    _seed(repository)
    application_id = repository.gateway_applications[0]["id"]
    try:
        assert client.put(
            OWNERSHIP.format(application_id),
            json={"owner_id": PERSON, "department_id": None},
        ).status_code == 200
        for department in ("department-platform", "department-commerce"):
            assert PERSON not in _people(department)
    finally:
        _uninstall()


def test_moving_the_channel_moves_the_person() -> None:
    repository = InMemoryRepository()
    _seed(repository)
    application_id = repository.gateway_applications[0]["id"]
    try:
        client.put(
            OWNERSHIP.format(application_id),
            json={"owner_id": PERSON, "department_id": "department-platform"},
        )
        assert PERSON in _people("department-platform")
        client.put(
            OWNERSHIP.format(application_id),
            json={"owner_id": PERSON, "department_id": "department-finance"},
        )
        assert PERSON not in _people("department-platform")
        assert PERSON in _people("department-finance")
    finally:
        _uninstall()


def test_the_recorded_department_wins_over_a_self_declared_one() -> None:
    """The one place this overrides something, and the reason it should.

    A department on a channel was typed by an administrator. A department on a request was
    declared by whatever sent the request, which is a header anyone holding the key can set.
    """
    repository = InMemoryRepository()
    _seed(repository)
    application_id = repository.gateway_applications[0]["id"]
    repository.usage_records.clear()
    try:
        client.put(
            OWNERSHIP.format(application_id),
            json={"owner_id": PERSON, "department_id": "department-security"},
        )
        catalog = client.get("/api/v1/enterprise/entities").json()
        entry = next(item for item in catalog["users"] if item["id"] == PERSON)
        assert entry["parent_id"] == "department-security"
    finally:
        _uninstall()


def test_a_retired_channel_stops_contributing_its_owner() -> None:
    repository = InMemoryRepository()
    _seed(repository)
    application_id = repository.gateway_applications[0]["id"]
    try:
        client.put(
            OWNERSHIP.format(application_id),
            json={"owner_id": PERSON, "department_id": "department-platform"},
        )
        assert PERSON in _people("department-platform")
        repository.gateway_applications[0]["status"] = "retired"
        assert PERSON not in _people("department-platform")
    finally:
        _uninstall()
