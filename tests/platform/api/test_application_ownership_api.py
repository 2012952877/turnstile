from __future__ import annotations

from datetime import UTC, datetime

from backend.api import app, application_access_service
from backend.http.dependencies import get_repository
from backend.http.session import SessionIdentity, require_authenticated_session
from tests.backend.model_platform.control_plane_support import APIM_ID
from tests.platform.api.api_support import client
from turnstile_core.domain.application_access import (
    GatewayApplicationDiscovery,
    GatewayApplicationDiscoveryItem,
    suggested_owner_id,
)
from turnstile_core.persistence.in_memory import InMemoryRepository
from turnstile_core.services.application_access import ApplicationAccessService

pytest_plugins = ("tests.platform.api.api_fixtures",)

OWNERSHIP = "/api/v1/application-access/applications/{0}/ownership"

# The shape an install that names subscriptions after people actually produces: the
# address is in the display name a human typed, and the slug has had its dots and its
# `@` flattened into dashes by APIM's id rules.
PERSON_NAMED = GatewayApplicationDiscoveryItem(
    apim_subscription_id="sub-a-aiginin-insilicomedicine-com",
    display_name="a.aiginin@insilicomedicine.com - IT Databricks Claude API",
    state="active",
    scope_type="product",
    scope_id="finops-applications",
    scope_exists=True,
    application_type="service",
    system_managed=False,
)
UNNAMED = GatewayApplicationDiscoveryItem(
    apim_subscription_id="aiclaw",
    display_name="AIClaw",
    state="active",
    scope_type="product",
    scope_id="finops-applications",
    scope_exists=True,
    application_type="service",
    system_managed=False,
)


def _seed(
    repository: InMemoryRepository,
    *items: GatewayApplicationDiscoveryItem,
    discovered_at: datetime | None = None,
) -> ApplicationAccessService:
    service = ApplicationAccessService(repository, sync_available=True)
    service.sync_discovery(
        GatewayApplicationDiscovery(
            gateway_profile_id=APIM_ID,
            discovered_at=discovered_at or datetime(2026, 9, 20, 2, tzinfo=UTC),
            items=list(items) or [PERSON_NAMED],
        ),
        "application-sync-worker",
    )
    return service


def _install(repository: InMemoryRepository, service: ApplicationAccessService) -> None:
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[application_access_service] = lambda: service


def _uninstall() -> None:
    app.dependency_overrides.pop(application_access_service, None)
    app.dependency_overrides.pop(get_repository, None)
    app.dependency_overrides.pop(require_authenticated_session, None)


def test_adopted_channels_arrive_unattributed_and_carry_a_suggestion() -> None:
    """Sync files a channel under nobody, and says who the name claims it is.

    Both halves matter. A key adopted from the gateway has no owner because nothing
    about it is evidence of one, and it has a suggestion because the address sitting
    in its name is the only lead an administrator has for several hundred of them.
    """
    repository = InMemoryRepository()
    service = _seed(repository, PERSON_NAMED, UNNAMED)
    _install(repository, service)
    try:
        items = client.get("/api/v1/application-access/applications").json()["items"]
        by_slug = {item["slug"]: item for item in items}

        named = by_slug["sub-a-aiginin-insilicomedicine-com"]
        assert named["owner_id"] is None
        assert named["department_id"] is None
        assert named["department_name"] is None
        assert named["owner_suggestion"] == "a.aiginin@insilicomedicine.com"

        assert by_slug["aiclaw"]["owner_suggestion"] is None
    finally:
        _uninstall()


def test_owner_attributes_a_channel_and_the_trail_names_who_said_so() -> None:
    repository = InMemoryRepository()
    service = _seed(repository)
    application_id = repository.gateway_applications[0]["id"]
    _install(repository, service)
    try:
        response = client.put(
            OWNERSHIP.format(application_id),
            json={
                "owner_id": "a.aiginin@insilicomedicine.com",
                "department_id": "department-platform",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["owner_id"] == "a.aiginin@insilicomedicine.com"
        assert body["department_id"] == "department-platform"
        # Resolved from the catalog, so the screen shows a department rather than a key.
        assert body["department_name"] == "AI Platform"

        trail = [
            item for item in repository.gateway_application_audit
            if item["operation"] == "updated"
        ]
        assert len(trail) == 1
        assert trail[0]["actor"] == "owner@contoso.com"
        assert trail[0]["before_state"] == {"owner_id": None, "department_id": None}
        assert trail[0]["after_state"]["department_id"] == "department-platform"
    finally:
        _uninstall()


def test_a_department_that_does_not_exist_is_refused_and_nothing_is_written() -> None:
    """The check that keeps the bridge from pointing at nothing.

    `department_id` is a join key into organization -> department -> user. A channel
    filed under a department that is not in the catalog is filed nowhere, while still
    reading on screen as though someone had attributed it -- which is worse than the
    blank it replaced.
    """
    repository = InMemoryRepository()
    service = _seed(repository)
    application_id = repository.gateway_applications[0]["id"]
    _install(repository, service)
    try:
        response = client.put(
            OWNERSHIP.format(application_id),
            json={
                "owner_id": "a.aiginin@insilicomedicine.com",
                "department_id": "department-does-not-exist",
            },
        )
        assert response.status_code == 422
        assert "department-does-not-exist" in response.json()["detail"]

        stored = repository.gateway_applications[0]
        assert stored["owner_id"] is None
        assert stored["department_id"] is None
        assert not [
            item for item in repository.gateway_application_audit
            if item["operation"] == "updated"
        ]
    finally:
        _uninstall()


def test_an_owner_that_is_not_an_address_is_refused() -> None:
    repository = InMemoryRepository()
    service = _seed(repository)
    application_id = repository.gateway_applications[0]["id"]
    _install(repository, service)
    try:
        for candidate in ("IT team", "robin", "system-runtime-health-check"):
            response = client.put(
                OWNERSHIP.format(application_id), json={"owner_id": candidate}
            )
            assert response.status_code == 422, candidate
        assert repository.gateway_applications[0]["owner_id"] is None
    finally:
        _uninstall()


def test_re_syncing_the_gateway_does_not_overwrite_an_attribution() -> None:
    """The regression this file exists for.

    Attribution is manual and sync runs on a button anyone can press. If a later
    discovery reset these columns the work would disappear without an error, and the
    only evidence would be a screen that used to say AI Platform and now says nothing.
    """
    repository = InMemoryRepository()
    service = _seed(repository)
    application_id = repository.gateway_applications[0]["id"]
    _install(repository, service)
    try:
        client.put(
            OWNERSHIP.format(application_id),
            json={
                "owner_id": "a.aiginin@insilicomedicine.com",
                "department_id": "department-platform",
            },
        )
        # Same subscription, later discovery, renamed upstream.
        service.sync_discovery(
            GatewayApplicationDiscovery(
                gateway_profile_id=APIM_ID,
                discovered_at=datetime(2026, 9, 21, 2, tzinfo=UTC),
                items=[
                    PERSON_NAMED.model_copy(
                        update={"display_name": "a.aiginin@insilicomedicine.com - renamed"}
                    )
                ],
            ),
            "application-sync-worker",
        )
        stored = repository.gateway_applications[0]
        assert stored["display_name"].endswith("renamed")
        assert stored["owner_id"] == "a.aiginin@insilicomedicine.com"
        assert stored["department_id"] == "department-platform"
    finally:
        _uninstall()


def test_attribution_can_be_cleared_when_the_person_leaves() -> None:
    repository = InMemoryRepository()
    service = _seed(repository)
    application_id = repository.gateway_applications[0]["id"]
    _install(repository, service)
    try:
        client.put(
            OWNERSHIP.format(application_id),
            json={
                "owner_id": "a.aiginin@insilicomedicine.com",
                "department_id": "department-platform",
            },
        )
        response = client.put(
            OWNERSHIP.format(application_id),
            json={"owner_id": None, "department_id": None},
        )
        assert response.status_code == 200
        assert response.json()["owner_id"] is None
        assert response.json()["department_id"] is None
        # The suggestion survives: the name still says who it was, and that is the
        # point of a suggestion -- it describes the label, not the decision.
        assert response.json()["owner_suggestion"] == "a.aiginin@insilicomedicine.com"
    finally:
        _uninstall()


def test_re_confirming_the_same_owner_files_no_audit_row() -> None:
    repository = InMemoryRepository()
    service = _seed(repository)
    application_id = repository.gateway_applications[0]["id"]
    _install(repository, service)
    try:
        body = {
            "owner_id": "a.aiginin@insilicomedicine.com",
            "department_id": "department-platform",
        }
        assert client.put(OWNERSHIP.format(application_id), json=body).status_code == 200
        assert client.put(OWNERSHIP.format(application_id), json=body).status_code == 200
        assert len([
            item for item in repository.gateway_application_audit
            if item["operation"] == "updated"
        ]) == 1
    finally:
        _uninstall()


def test_attributing_a_channel_requires_owner_role() -> None:
    repository = InMemoryRepository()
    service = _seed(repository)
    application_id = repository.gateway_applications[0]["id"]
    _install(repository, service)
    app.dependency_overrides[require_authenticated_session] = lambda: SessionIdentity(
        id="member-id",
        email="member@contoso.com",
        name="Member",
        role="member",
        method="entra",
        session_expires_at=datetime(2026, 9, 27, tzinfo=UTC),
    )
    try:
        response = client.put(
            OWNERSHIP.format(application_id), json={"department_id": "department-platform"}
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Owner role is required"
        assert repository.gateway_applications[0]["department_id"] is None
    finally:
        _uninstall()


def test_a_suggestion_is_never_adopted_on_its_own() -> None:
    """The line this design turns on.

    Reading the list, listing it a second time, and syncing again all leave the owner
    empty. Only a request signed by an administrator fills it in.
    """
    repository = InMemoryRepository()
    service = _seed(repository)
    _install(repository, service)
    try:
        for _ in range(2):
            items = client.get("/api/v1/application-access/applications").json()["items"]
            assert items[0]["owner_suggestion"] == "a.aiginin@insilicomedicine.com"
            assert items[0]["owner_id"] is None
        service.sync_discovery(
            GatewayApplicationDiscovery(
                gateway_profile_id=APIM_ID,
                discovered_at=datetime(2026, 9, 22, 2, tzinfo=UTC),
                items=[PERSON_NAMED],
            ),
            "application-sync-worker",
        )
        assert repository.gateway_applications[0]["owner_id"] is None
    finally:
        _uninstall()


def test_suggestion_reads_the_display_name_and_never_the_slug() -> None:
    assert suggested_owner_id("a.aiginin@insilicomedicine.com - IT Databricks") == (
        "a.aiginin@insilicomedicine.com"
    )
    assert suggested_owner_id("Robin-IT") is None
    assert suggested_owner_id("AIClaw") is None
    # The slug is what APIM left behind after flattening the address. Which dashes
    # were dots and which one was the `@` is gone, so it is not read at all.
    assert suggested_owner_id("sub-a-aiginin-insilicomedicine-com") is None
    # Case is normalised so two administrators typing the same person agree.
    assert suggested_owner_id("A.Aiginin@Insilicomedicine.COM - key") == (
        "a.aiginin@insilicomedicine.com"
    )
