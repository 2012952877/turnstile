from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest

from backend.domain.application_access import (
    GatewayApplicationDiscovery,
    GatewayApplicationDiscoveryItem,
    UsageApplicationAttribution,
)
from backend.domain.models import TokenUsageRecord
from backend.integrations.ledger import (
    RESERVATION_PREFIX,
    ROLL_FORWARD_ACTOR,
    LedgerReservation,
    LedgerSyncService,
    TableStorageLedger,
    application_map_partition_key,
    application_partition_key,
    model_access_value,
    partition_key,
    period_start_for,
    reservation_row_key,
)
from backend.persistence.in_memory import InMemoryRepository
from backend.services.application_access import ApplicationAccessService

PERIOD_START = datetime(2026, 7, 1, tzinfo=UTC)
USER = "test.user01@contoso.com"


class FakeLedgerStore:
    def __init__(self) -> None:
        self.entities: dict[tuple[str, str], dict[str, Any]] = {}
        self.operations: list[tuple[str, str, str]] = []

    def upsert(self, partition: str, row_key: str, entity: dict[str, Any]) -> None:
        self.entities[(partition, row_key)] = entity
        self.operations.append(("upsert", partition, row_key))

    def list_reservations(self, partition: str) -> list[LedgerReservation]:
        return [
            LedgerReservation(
                row_key=row_key,
                correlation_id=row_key.split("|", 2)[2],
                reserved_tokens=int(entity["Reserved"]),
            )
            for (row_partition, row_key), entity in sorted(self.entities.items())
            if row_partition == partition and row_key.startswith(RESERVATION_PREFIX)
        ]

    def delete_reservations(self, partition: str, row_keys: Sequence[str]) -> int:
        deleted = 0
        for row_key in row_keys:
            self.operations.append(("delete", partition, row_key))
            if self.entities.pop((partition, row_key), None) is not None:
                deleted += 1
        return deleted


class FakeTokenProvider:
    def __init__(self) -> None:
        self.calls = 0

    def token(self, resource: str) -> str:
        assert resource == "https://storage.azure.com/"
        self.calls += 1
        return f"token-{self.calls}"


def _usage(
    suffix: str,
    ts: datetime,
    tokens: int,
    *,
    estimated: bool,
    status_code: int = 200,
    ingest_error: str | None = None,
) -> TokenUsageRecord:
    return TokenUsageRecord(
        id=f"usage-{suffix}",
        request_id=f"request-{suffix}",
        correlation_id=f"correlation-{suffix}",
        ts=ts,
        team="AI Platform",
        organization="Contoso Global",
        organization_id="org-contoso-global",
        department="AI Platform",
        department_id="department-platform",
        project="Model FinOps",
        project_id="project-finops",
        user=USER,
        user_id=USER,
        agent="Delivery Engineer",
        agent_id="agent-delivery",
        workflow="ledger-test",
        run_id=f"run-{suffix}",
        turn_index=1,
        provider="anthropic",
        model="databricks-claude-sonnet-5",
        model_id="databricks-claude-sonnet-5",
        runtime="Azure Databricks Claude via APIM",
        request_source="employee-desktop",
        input_tokens=tokens,
        cached_tokens=0,
        output_tokens=0,
        et=1,
        et_coeff_m=1,
        latency_ms=100,
        status="success" if status_code < 400 else "error",
        status_code=status_code,
        estimated_cost=0.0,
        estimated=estimated,
        ingest_source="eventhub",
        ingest_error=ingest_error,
    )


def _repository_with_budget() -> InMemoryRepository:
    repository = InMemoryRepository()
    for scope_type, scope_id, parent, limit in (
        ("organization", "org-contoso-global", None, 100_000),
        ("department", "department-platform", "org-contoso-global", 50_000),
        ("user", USER, "department-platform", 10_000),
    ):
        repository.upsert_token_budget(
            PERIOD_START.date(), scope_type, scope_id, parent, limit, 80, "test"
        )
    return repository


def test_snapshot_counts_full_stored_usage_without_a_global_watermark() -> None:
    repository = _repository_with_budget()
    repository.write_token_usage(
        _usage("final", datetime(2026, 7, 10, tzinfo=UTC), 400, estimated=False)
    )
    # Streamed and not yet reconciled: its real size is unknown, so it must not be
    # folded into the confirmed total.
    repository.write_token_usage(
        _usage("pending", datetime(2026, 7, 11, tzinfo=UTC), 0, estimated=True)
    )
    repository.write_token_usage(
        _usage("later", datetime(2026, 7, 12, tzinfo=UTC), 700, estimated=False)
    )

    service = LedgerSyncService(repository, FakeLedgerStore())
    person = service.snapshot(datetime(2026, 7, 27, 12, 0, tzinfo=UTC))[0]

    # The pending stream contributes its currently stored zero; its own R row covers the
    # unknown usage. Unrelated later requests are settled into C immediately.
    assert person.confirmed_tokens == 1_100
    assert person.token_limit == 10_000
    assert person.enforce is False


def test_snapshot_counts_final_usage_when_nothing_is_pending() -> None:
    repository = _repository_with_budget()
    repository.write_token_usage(
        _usage("a", datetime(2026, 7, 10, tzinfo=UTC), 250, estimated=False)
    )
    now = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)

    service = LedgerSyncService(repository, FakeLedgerStore())
    person = service.snapshot(now)[0]

    assert person.confirmed_tokens == 250


def test_sync_settles_by_correlation_without_head_of_line_blocking() -> None:
    repository = _repository_with_budget()
    repository.set_department_enforcement("department-platform", "block", "owner")
    repository.write_token_usage(
        _usage("final", datetime(2026, 7, 10, tzinfo=UTC), 400, estimated=False)
    )
    repository.write_token_usage(
        _usage("pending", datetime(2026, 7, 11, tzinfo=UTC), 0, estimated=True)
    )
    repository.write_token_usage(
        _usage("later", datetime(2026, 7, 12, tzinfo=UTC), 700, estimated=False)
    )

    store = FakeLedgerStore()
    partition = partition_key(USER, "2026-07")
    # Production ordering: R is written inbound, before the corresponding outbound usage ts.
    final = reservation_row_key(
        datetime(2026, 7, 10, 8, 59, tzinfo=UTC), "correlation-final"
    )
    pending = reservation_row_key(
        datetime(2026, 7, 11, 8, 59, tzinfo=UTC), "correlation-pending"
    )
    later = reservation_row_key(
        datetime(2026, 7, 12, 8, 59, tzinfo=UTC), "correlation-later"
    )
    store.entities[(partition, final)] = {"Reserved": 500}
    store.entities[(partition, pending)] = {"Reserved": 900}
    store.entities[(partition, later)] = {"Reserved": 800}

    service = LedgerSyncService(repository, store)
    outcome = service.run(datetime(2026, 7, 27, 12, 0, tzinfo=UTC))

    assert outcome.people == 1
    assert outcome.reservations_settled == 2
    assert store.entities[(partition, "Q")] == {"Limit": 10_000, "Enforce": True}
    assert store.entities[(partition, "C")] == {"ConfirmedUsed": 1_100}
    assert (partition, final) not in store.entities
    assert (partition, later) not in store.entities
    assert (partition, pending) in store.entities

    c_write = store.operations.index(("upsert", partition, "C"))
    first_delete = min(
        index for index, operation in enumerate(store.operations) if operation[0] == "delete"
    )
    assert c_write < first_delete


def test_application_ledger_projects_q_c_m_and_settles_exact_reservation() -> None:
    repository = InMemoryRepository()
    gateway_id = UUID("10000000-0000-4000-8000-000000000001")
    application = ApplicationAccessService(
        repository, sync_available=True
    ).sync_discovery(
        GatewayApplicationDiscovery(
            gateway_profile_id=gateway_id,
            discovered_at=datetime(2026, 7, 1, tzinfo=UTC),
            items=[
                GatewayApplicationDiscoveryItem(
                    apim_subscription_id="outline-assistant",
                    display_name="Outline Assistant",
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
    )[0]
    application_id = UUID(str(application["id"]))
    subscription = repository.gateway_application_subscriptions[0]
    usage = _usage(
        "application-final",
        datetime(2026, 7, 10, tzinfo=UTC),
        400,
        estimated=False,
    )
    repository.write_token_usage(
        usage,
        UsageApplicationAttribution(
            application_id=application_id,
            application_subscription_id=subscription["id"],
            application_name_snapshot="Outline Assistant",
            apim_subscription_id="outline-assistant",
            actor_type="service",
            actor_id="service:outline-assistant",
        ),
    )
    model = repository.models[0]
    model_id = UUID(str(model["id"]))
    repository.gateway_application_model_policies[application_id] = {
        "application_id": application_id,
        "updated_by": "owner",
        "updated_at": datetime(2026, 7, 1, tzinfo=UTC),
    }
    repository.gateway_application_model_access[application_id] = {model_id}
    store = FakeLedgerStore()
    partition = application_partition_key(application_id, "2026-07")
    reservation = reservation_row_key(
        datetime(2026, 7, 10, 8, 59, tzinfo=UTC), usage.correlation_id
    )
    store.entities[(partition, reservation)] = {"Reserved": 500}

    outcome = LedgerSyncService(repository, store).run(
        datetime(2026, 7, 27, 12, tzinfo=UTC)
    )

    assert outcome.applications == 1
    assert outcome.application_reservations_settled == 1
    assert outcome.application_model_policies == 1
    assert outcome.application_mappings == 1
    assert store.entities[(partition, "Q")] == {
        "Limit": 100_000,
        "TokensPerMinute": 100_000,
        "Enforce": True,
    }
    assert store.entities[(partition, "C")] == {"ConfirmedUsed": 400}
    assert (partition, reservation) not in store.entities
    assert store.entities[(partition, "M")] == {
        "Configured": True,
        "Models": model_access_value([str(model_id), str(model["model_key"])]),
    }
    assert store.entities[
        (application_map_partition_key(gateway_id), "outline-assistant")
    ] == {
        "ApplicationId": str(application_id),
        "ApplicationSlug": "outline-assistant",
        "ApplicationName": "Outline Assistant",
        "ApplicationType": "service",
        "ApplicationStatus": "active",
        "SubscriptionState": "active",
        "ScopeExists": True,
    }
    c_write = store.operations.index(("upsert", partition, "C"))
    reservation_delete = store.operations.index(("delete", partition, reservation))
    assert c_write < reservation_delete


def test_application_budget_roll_forward_is_idempotent() -> None:
    repository = InMemoryRepository()
    gateway_id = UUID("10000000-0000-4000-8000-000000000001")
    application = ApplicationAccessService(
        repository, sync_available=True
    ).sync_discovery(
        GatewayApplicationDiscovery(
            gateway_profile_id=gateway_id,
            discovered_at=datetime(2026, 7, 1, tzinfo=UTC),
            items=[
                GatewayApplicationDiscoveryItem(
                    apim_subscription_id="outline-assistant",
                    display_name="Outline Assistant",
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
    )[0]
    application_id = UUID(str(application["id"]))
    repository.gateway_application_budgets[
        (date(2026, 7, 1), application_id)
    ]["token_limit"] = 750_000

    first = repository.roll_forward_gateway_application_budgets(
        date(2026, 8, 1), ROLL_FORWARD_ACTOR
    )
    second = repository.roll_forward_gateway_application_budgets(
        date(2026, 8, 1), ROLL_FORWARD_ACTOR
    )

    assert first == 1
    assert second == 0
    assert repository.gateway_application_budgets[
        (date(2026, 8, 1), application_id)
    ]["token_limit"] == 750_000


def test_missing_telemetry_keeps_only_its_own_reservation() -> None:
    repository = _repository_with_budget()
    store = FakeLedgerStore()
    partition = partition_key(USER, "2026-07")
    orphan = reservation_row_key(
        datetime(2026, 7, 10, tzinfo=UTC), "correlation-not-ingested"
    )
    store.entities[(partition, orphan)] = {"Reserved": 9_000}

    outcome = LedgerSyncService(repository, store).run(
        datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    )

    assert outcome.reservations_settled == 0
    assert (partition, orphan) in store.entities
    assert store.entities[(partition, "C")] == {"ConfirmedUsed": 0}


def test_failed_request_settles_its_zero_usage_reservation() -> None:
    repository = _repository_with_budget()
    repository.write_token_usage(
        _usage(
            "failed",
            datetime(2026, 7, 10, tzinfo=UTC),
            0,
            estimated=True,
            status_code=500,
        )
    )
    store = FakeLedgerStore()
    partition = partition_key(USER, "2026-07")
    failed = reservation_row_key(
        datetime(2026, 7, 10, 8, 59, tzinfo=UTC), "correlation-failed"
    )
    store.entities[(partition, failed)] = {"Reserved": 900}

    outcome = LedgerSyncService(repository, store).run(
        datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    )

    assert outcome.reservations_settled == 1
    assert (partition, failed) not in store.entities


def test_partially_reconciled_stream_waits_for_its_user_cache_bucket() -> None:
    repository = _repository_with_budget()
    repository.write_token_usage(
        _usage(
            "partial-stream",
            datetime(2026, 7, 10, tzinfo=UTC),
            400,
            estimated=True,
            ingest_error="stream_cache_usage_unavailable",
        )
    )
    store = FakeLedgerStore()
    partition = partition_key(USER, "2026-07")
    reservation = reservation_row_key(
        datetime(2026, 7, 10, 8, 59, tzinfo=UTC), "correlation-partial-stream"
    )
    store.entities[(partition, reservation)] = {"Reserved": 900}

    outcome = LedgerSyncService(repository, store).run(
        datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    )

    assert outcome.reservations_settled == 0
    assert store.entities[(partition, "C")] == {"ConfirmedUsed": 400}
    assert (partition, reservation) in store.entities


def test_legacy_user_cache_metric_does_not_settle_a_stream_reservation() -> None:
    repository = _repository_with_budget()
    usage = _usage(
        "partial-stream",
        datetime(2026, 7, 10, tzinfo=UTC),
        400,
        estimated=True,
        ingest_error="stream_cache_usage_unavailable",
    )
    repository.write_token_usage(usage)
    repository.apim_cache_read_buckets[
        (
            "turnstile-llm",
            "user",
            USER,
            usage.ts.replace(minute=0, second=0, microsecond=0),
        )
    ] = 600
    store = FakeLedgerStore()
    partition = partition_key(USER, "2026-07")
    reservation = reservation_row_key(
        datetime(2026, 7, 10, 8, 59, tzinfo=UTC), "correlation-partial-stream"
    )
    store.entities[(partition, reservation)] = {"Reserved": 900}

    outcome = LedgerSyncService(repository, store).run(
        datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    )

    assert outcome.reservations_settled == 0
    assert store.entities[(partition, "C")] == {"ConfirmedUsed": 400}
    assert (partition, reservation) in store.entities


def test_removed_budget_is_disabled_and_its_reservations_still_settle() -> None:
    repository = _repository_with_budget()
    repository.write_token_usage(
        _usage("final", datetime(2026, 7, 10, tzinfo=UTC), 400, estimated=False)
    )
    repository.delete_token_budget(PERIOD_START.date(), "user", USER, "owner")
    store = FakeLedgerStore()
    partition = partition_key(USER, "2026-07")
    final = reservation_row_key(
        datetime(2026, 7, 10, 8, 59, tzinfo=UTC), "correlation-final"
    )
    store.entities[(partition, "Q")] = {"Limit": 10_000, "Enforce": True}
    store.entities[(partition, final)] = {"Reserved": 500}

    outcome = LedgerSyncService(repository, store).run(
        datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    )

    assert outcome.reservations_settled == 1
    assert store.entities[(partition, "Q")] == {"Limit": 0, "Enforce": False}
    assert store.entities[(partition, "C")] == {"ConfirmedUsed": 400}
    assert (partition, final) not in store.entities


def test_current_usage_is_written_before_previous_month_reservation_is_deleted() -> None:
    repository = _repository_with_budget()
    repository.roll_forward_budgets(date(2026, 8, 1), ROLL_FORWARD_ACTOR)
    repository.write_token_usage(
        _usage("cross-month", datetime(2026, 8, 1, 0, 0, 2, tzinfo=UTC), 600, estimated=False)
    )
    store = FakeLedgerStore()
    july_partition = partition_key(USER, "2026-07")
    august_partition = partition_key(USER, "2026-08")
    july_reservation = reservation_row_key(
        datetime(2026, 7, 31, 23, 59, 58, tzinfo=UTC), "correlation-cross-month"
    )
    store.entities[(july_partition, july_reservation)] = {"Reserved": 900}

    outcome = LedgerSyncService(repository, store).run(
        datetime(2026, 8, 1, 0, 5, tzinfo=UTC)
    )

    assert outcome.reservations_settled == 1
    assert store.entities[(august_partition, "C")] == {"ConfirmedUsed": 600}
    current_write = store.operations.index(("upsert", august_partition, "C"))
    previous_delete = store.operations.index(("delete", july_partition, july_reservation))
    assert current_write < previous_delete


def test_reservation_row_keys_sort_chronologically() -> None:
    earlier = reservation_row_key(datetime(2026, 7, 9, 23, 59, 59, tzinfo=UTC), "z")
    later = reservation_row_key(datetime(2026, 7, 10, 0, 0, 0, tzinfo=UTC), "a")
    assert earlier < later


def test_table_reservation_listing_follows_continuation_and_reuses_auth() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "RowKey": "R|2026-07-10T09:00:00.000Z|correlation-a",
                            "Reserved": 500,
                        }
                    ]
                },
                headers={
                    "x-ms-continuation-NextPartitionKey": "next-partition",
                    "x-ms-continuation-NextRowKey": "next-row",
                },
            )
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "RowKey": "R|2026-07-10T09:01:00.000Z|correlation-b",
                        "Reserved": 700,
                    }
                ]
            },
        )

    token_provider = FakeTokenProvider()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    store = TableStorageLedger(
        "https://ledger.table.core.windows.net",
        "TurnstileLedger",
        token_provider=token_provider,
        client=client,
    )

    rows = store.list_reservations("person@example.com|2026-07")

    assert [(row.correlation_id, row.reserved_tokens) for row in rows] == [
        ("correlation-a", 500),
        ("correlation-b", 700),
    ]
    assert len(requests) == 2
    assert requests[1].url.params["NextPartitionKey"] == "next-partition"
    assert requests[1].url.params["NextRowKey"] == "next-row"
    assert token_provider.calls == 1
    client.close()


def test_table_request_refreshes_managed_identity_token_once_after_unauthorized() -> None:
    authorizations: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        authorizations.append(request.headers["Authorization"])
        if len(authorizations) == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"value": []})

    token_provider = FakeTokenProvider()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    store = TableStorageLedger(
        "https://ledger.table.core.windows.net",
        "TurnstileLedger",
        token_provider=token_provider,
        client=client,
    )

    assert store.list_reservations("person@example.com|2026-07") == []
    assert authorizations == ["Bearer token-1", "Bearer token-2"]
    assert token_provider.calls == 2
    client.close()


def test_table_reservation_deletes_are_partition_batched_at_one_hundred() -> None:
    batch_sizes: list[int] = []
    request_bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/$batch"
        count = request.content.count(b"DELETE ")
        batch_sizes.append(count)
        request_bodies.append(request.content)
        return httpx.Response(202, text="HTTP/1.1 204 No Content\r\n" * count)

    token_provider = FakeTokenProvider()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    store = TableStorageLedger(
        "https://ledger.table.core.windows.net",
        "TurnstileLedger",
        token_provider=token_provider,
        client=client,
    )
    row_keys = [
        reservation_row_key(
            datetime(2026, 7, 10, 9, 0, microsecond=index * 1000, tzinfo=UTC),
            f"correlation-{index}",
        )
        for index in range(205)
    ]

    deleted = store.delete_reservations("person@example.com|2026-07", row_keys)

    assert deleted == 205
    assert batch_sizes == [100, 100, 5]
    first = request_bodies[0].decode()
    outer_boundary = first.split("\r\n", 1)[0][2:]
    assert first.startswith(f"--{outer_boundary}\r\nContent-Type: multipart/mixed;")
    assert "\r\nContent-Type: application/http\r\n" in first
    assert "\r\nContent-Transfer-Encoding: binary\r\n" in first
    assert "\r\nIf-Match: *\r\n" in first
    assert "\r\nDataServiceVersion: 3.0;\r\n" in first
    assert "PartitionKey='person%40example.com%7C2026-07'" in first
    assert first.endswith(f"--{outer_boundary}--\r\n")
    assert token_provider.calls == 1
    client.close()


def test_table_batch_inner_failure_is_not_mistaken_for_outer_accepted() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(202, text="HTTP/1.1 400 Bad Request\r\n")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    store = TableStorageLedger(
        "https://ledger.table.core.windows.net",
        "TurnstileLedger",
        token_provider=FakeTokenProvider(),
        client=client,
    )

    with pytest.raises(RuntimeError, match=r"statuses=\[400\]"):
        store.delete_reservations(
            "person@example.com|2026-07",
            [
                reservation_row_key(
                    datetime(2026, 7, 10, 9, 0, tzinfo=UTC), "correlation-failed"
                )
            ],
        )
    client.close()


def test_model_access_projects_both_identifier_spaces() -> None:
    # The gateway sees a registry UUID from the dashboard BFF and a model key from a
    # desktop client for the same model, and cannot resolve one to the other. Projecting
    # only one space would deny the caller that happens to use the other.
    repository = InMemoryRepository()
    model = next(item for item in repository.models if item["model_key"] == "gpt-5.6-luna")
    repository.bulk_upsert_user_budgets(
        PERIOD_START.date(),
        "department-platform",
        [],
        80,
        "owner",
        selected_user_ids=[USER],
        model_ids=[model["id"]],
    )

    store = FakeLedgerStore()
    service = LedgerSyncService(repository, store)
    service.project_model_access(
        service.model_access(), datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    )

    row = store.entities[(partition_key(USER, "2026-07"), "M")]
    assert row["Configured"] is True
    assert f"|{str(model['id']).lower()}|" in row["Models"]
    assert f"|{model['model_key'].lower()}|" in row["Models"]


def test_deny_all_projects_an_empty_membership_set_not_a_missing_row() -> None:
    # Deny-all and "never configured" must not look the same in the ledger: the first has
    # to block everything, the second has to allow everything. An absent row means the
    # latter, so an explicit policy with no models still needs a row.
    repository = InMemoryRepository()
    repository.bulk_upsert_user_budgets(
        PERIOD_START.date(),
        "department-platform",
        [],
        80,
        "owner",
        selected_user_ids=[USER],
        model_ids=[],
    )

    store = FakeLedgerStore()
    service = LedgerSyncService(repository, store)
    service.project_model_access(
        service.model_access(), datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    )

    row = store.entities[(partition_key(USER, "2026-07"), "M")]
    assert row["Configured"] is True
    # Nothing can be a member of this, which is what deny-all means.
    assert row["Models"] == "||"


def test_model_access_value_cannot_match_a_prefix_of_a_longer_identifier() -> None:
    value = model_access_value(["gpt-5.6-luna"])
    assert "|gpt-5.6-luna|" in value
    assert "|gpt-5.6|" not in value


AUGUST = datetime(2026, 8, 1, tzinfo=UTC)


def test_a_new_period_inherits_the_previous_allowances_instead_of_enforcing_nothing() -> None:
    # The defect this exists for: budgets are period-scoped, enforcement mode is not, and
    # both admission paths read a missing budget row as "not blocked". Without the
    # carry-forward a department configured to block stops blocking every 1st of the month.
    repository = _repository_with_budget()
    store = FakeLedgerStore()
    service = LedgerSyncService(repository, store)

    assert service.snapshot(AUGUST) == []

    rolled = repository.roll_forward_budgets(
        period_start_for(AUGUST), ROLL_FORWARD_ACTOR
    )

    assert rolled is not None and rolled["scope_count"] == 3
    service.run(AUGUST)
    quota = store.entities[(partition_key(USER, "2026-08"), "Q")]
    assert quota["Limit"] == 10_000


def test_the_period_inherits_only_once_so_a_removal_is_not_undone() -> None:
    # The timer runs every five minutes. Re-copying would resurrect an allowance the
    # administrator had just deliberately removed, which is why the marker is written
    # even when nothing was copied.
    repository = _repository_with_budget()
    period = period_start_for(AUGUST)
    repository.roll_forward_budgets(period, ROLL_FORWARD_ACTOR)

    repository.delete_token_budget(period, "user", USER, "owner")
    second = repository.roll_forward_budgets(period, ROLL_FORWARD_ACTOR)

    assert second is None
    service = LedgerSyncService(repository, FakeLedgerStore())
    retired = service.snapshot(AUGUST)
    assert len(retired) == 1
    assert retired[0].user_id == USER
    assert retired[0].token_limit == 0
    assert retired[0].enforce is False


def test_a_period_already_being_configured_is_left_alone() -> None:
    # Copying into a half-configured month could push inherited children past a parent the
    # administrator had just lowered, and that invariant is only checked on write.
    repository = _repository_with_budget()
    period = period_start_for(AUGUST)
    repository.upsert_token_budget(
        period, "organization", "org-contoso-global", None, 20_000, 80, "owner"
    )

    rolled = repository.roll_forward_budgets(period, ROLL_FORWARD_ACTOR)

    assert rolled is not None and rolled["scope_count"] == 0
    assert rolled["source_period_start"] is None
    assert repository.token_budgets[(period, "organization", "org-contoso-global")][
        "token_limit"
    ] == 20_000
    assert (period, "user", USER) not in repository.token_budgets


def test_the_inherited_allowance_is_audited_as_its_own_act() -> None:
    # An allowance that appears with no explanation reads as a bug. The timeline has to be
    # able to say the month inherited it, and from whom.
    repository = _repository_with_budget()
    period = period_start_for(AUGUST)

    repository.roll_forward_budgets(period, ROLL_FORWARD_ACTOR)

    events = repository.list_token_budget_audit(period, 20)
    assert len(events) == 3
    assert {event["action"] for event in events} == {"assigned"}
    actors = {event["changed_by"] for event in events}
    assert actors == {"system-budget-roll-forward"}
    # A machine identity with no `@` can never be listed as a person who owns a budget.
    assert not any("@" in actor for actor in actors)
