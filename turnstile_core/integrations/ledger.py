"""Projects the PostgreSQL budget state into an Azure Table Storage ledger.

The APIM admission check reads this ledger on every employee request, so nothing in
this application may sit on the inference path. The Function pushes state outwards on
a timer instead; APIM never calls back.

Row layout, one partition per person per month:

    PartitionKey  "<user id>|<YYYY-MM>"

    RowKey  "Q"                       Limit, Enforce            written here
    RowKey  "C"                       ConfirmedUsed             written here
    RowKey  "M"                       Configured, Models        written here
    RowKey  "R|<iso ts>|<correlation>"  Reserved                  written by APIM

    used = C.ConfirmedUsed + sum(R.Reserved)

The Function settles reservations by correlation ID, never by a shared time frontier. It
writes the full stored usage total to C first, then deletes only reservations whose exact
request is final in PostgreSQL. A partial failure can temporarily double-count usage but
can never make usage disappear.

The M row rides the same partition deliberately: the policy already reads the whole
partition in one filtered GET, so model access costs no additional round trip.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx

from ..persistence.repository import QueryRepository
from .reconciliation import ManagedIdentityTokenProvider

logger = logging.getLogger(__name__)

STORAGE_RESOURCE = "https://storage.azure.com/"
# Table Storage rejects Atom without this, and bearer auth needs at least this version.
TABLE_API_VERSION = "2020-12-06"
RESERVATION_PREFIX = "R|"
MODEL_ACCESS_ROW_KEY = "M"
# Both ends are delimited so a membership test cannot match a prefix of a longer id.
MODEL_DELIMITER = "|"
# Table Storage caps a string property at 32k characters. A registry that large is not a
# real configuration, and silently truncating the list would turn into a silent denial, so
# an oversized set is projected as unconfigured instead and logged.
MODEL_PROPERTY_LIMIT = 30000
# Carries no `@`, so `merge_observed_users` can never list the roll-forward as a person
# who owns a budget — the same trick the other machine identities use.
ROLL_FORWARD_ACTOR = "system-budget-roll-forward"
APPLICATION_PARTITION_PREFIX = "app|"
APPLICATION_MAP_PARTITION_PREFIX = "app-map|"


def period_start_for(now: datetime) -> date:
    """First day of the month `now` falls in, in UTC.

    Shared so the scheduler and the ledger projection cannot disagree about which period
    they are working on.
    """
    return date(now.year, now.month, 1)


def model_access_value(identifiers: Sequence[str]) -> str:
    """Delimited membership set the policy can test with a single Contains().

    Case is folded here rather than in the policy: a model key is caller-supplied text and
    the comparison has to be stable without asking a policy expression to normalise.
    """
    unique = sorted({value.strip().lower() for value in identifiers if value.strip()})
    return MODEL_DELIMITER + MODEL_DELIMITER.join(unique) + MODEL_DELIMITER


def ledger_stamp(ts: datetime) -> str:
    """Fixed-width UTC stamp retained in row keys for ordering and diagnostics."""
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def reservation_row_key(ts: datetime, request_id: str) -> str:
    return f"{RESERVATION_PREFIX}{ledger_stamp(ts)}|{request_id}"


def partition_key(user_id: str, period: str) -> str:
    return f"{user_id}|{period}"


def application_partition_key(application_id: UUID | str, period: str) -> str:
    return f"{APPLICATION_PARTITION_PREFIX}{application_id}|{period}"


def application_map_partition_key(gateway_profile_id: UUID | str) -> str:
    return f"{APPLICATION_MAP_PARTITION_PREFIX}{gateway_profile_id}"


@dataclass(frozen=True)
class LedgerPerson:
    user_id: str
    department_id: str
    token_limit: int
    confirmed_tokens: int
    enforce: bool


@dataclass(frozen=True)
class LedgerReservation:
    row_key: str
    correlation_id: str
    reserved_tokens: int


@dataclass(frozen=True)
class LedgerModelAccess:
    user_id: str
    identifiers: tuple[str, ...]


@dataclass(frozen=True)
class LedgerApplication:
    application_id: str
    token_limit: int
    tokens_per_minute: int
    confirmed_tokens: int
    enforce: bool


@dataclass(frozen=True)
class LedgerApplicationModelAccess:
    application_id: str
    identifiers: tuple[str, ...]


@dataclass(frozen=True)
class LedgerSyncOutcome:
    people: int
    reservations_settled: int
    model_policies: int = 0
    applications: int = 0
    application_reservations_settled: int = 0
    application_model_policies: int = 0
    application_mappings: int = 0


class LedgerStore(Protocol):
    def upsert(self, partition: str, row_key: str, entity: dict[str, Any]) -> None: ...

    def list_reservations(self, partition: str) -> Sequence[LedgerReservation]: ...

    def delete_reservations(self, partition: str, row_keys: Sequence[str]) -> int: ...


class StorageTokenProvider(Protocol):
    def token(self, resource: str) -> str: ...


class TableStorageLedger:
    """Minimal Table Storage client over the managed identity, with no SDK dependency."""

    def __init__(
        self,
        endpoint: str,
        table: str,
        token_provider: StorageTokenProvider | None = None,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._table = table
        self._token_provider = token_provider or ManagedIdentityTokenProvider()
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None
        self._access_token: str | None = None

    def __enter__(self) -> TableStorageLedger:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _headers(self) -> dict[str, str]:
        if self._access_token is None:
            self._access_token = self._token_provider.token(STORAGE_RESOURCE)
        return {
            "Authorization": f"Bearer {self._access_token}",
            "x-ms-version": TABLE_API_VERSION,
            "x-ms-client-request-id": str(uuid4()),
            "Accept": "application/json;odata=nometadata",
            "Accept-Charset": "UTF-8",
            "DataServiceVersion": "3.0;",
            "MaxDataServiceVersion": "3.0;NetFx",
            "Content-Type": "application/json",
        }

    def _entity_url(self, partition: str, row_key: str) -> str:
        return (
            f"{self._endpoint}/{self._table}"
            f"(PartitionKey='{quote(partition, safe='')}',"
            f"RowKey='{quote(row_key, safe='')}')"
        )

    def _request(
        self, method: str, url: str, extra_headers: dict[str, str] | None = None, **kwargs: Any
    ) -> httpx.Response:
        headers = {**self._headers(), **(extra_headers or {})}
        response = self._client.request(method, url, headers=headers, **kwargs)
        if response.status_code == 401:
            self._access_token = None
            headers = {**self._headers(), **(extra_headers or {})}
            response = self._client.request(method, url, headers=headers, **kwargs)
        return response

    def upsert(self, partition: str, row_key: str, entity: dict[str, Any]) -> None:
        response = self._request(
            "PUT", self._entity_url(partition, row_key), json=entity
        )
        response.raise_for_status()

    def list_reservations(self, partition: str) -> list[LedgerReservation]:
        """List every reservation in one partition, following Table continuation tokens."""
        escaped_partition = partition.replace("'", "''")
        params: dict[str, str] = {
            "$filter": (
                f"PartitionKey eq '{escaped_partition}' "
                f"and RowKey ge '{RESERVATION_PREFIX}' and RowKey lt 'S'"
            ),
            "$select": "RowKey,Reserved",
        }
        reservations: list[LedgerReservation] = []
        while True:
            response = self._request(
                "GET", f"{self._endpoint}/{self._table}()", params=params
            )
            response.raise_for_status()
            for row in response.json().get("value", []):
                row_key = str(row.get("RowKey", ""))
                parts = row_key.split("|", 2)
                if len(parts) != 3 or parts[0] != "R" or not parts[2]:
                    logger.warning("Malformed reservation row key skipped: %s", row_key)
                    continue
                reservations.append(
                    LedgerReservation(
                        row_key=row_key,
                        correlation_id=parts[2],
                        reserved_tokens=int(row.get("Reserved", 0)),
                    )
                )
            next_partition = response.headers.get("x-ms-continuation-nextpartitionkey")
            if not next_partition:
                break
            params["NextPartitionKey"] = next_partition
            next_row = response.headers.get("x-ms-continuation-nextrowkey")
            if next_row:
                params["NextRowKey"] = next_row
            else:
                params.pop("NextRowKey", None)
        return reservations

    @staticmethod
    def _batch_delete_body(
        endpoint: str, table: str, partition: str, row_keys: Sequence[str]
    ) -> tuple[str, bytes]:
        batch = f"batch_{uuid4().hex}"
        changeset = f"changeset_{uuid4().hex}"
        lines = [
            f"--{batch}",
            f"Content-Type: multipart/mixed; boundary={changeset}",
            "",
        ]
        for content_id, row_key in enumerate(row_keys, start=1):
            entity = (
                f"{endpoint}/{table}"
                f"(PartitionKey='{quote(partition, safe='')}',"
                f"RowKey='{quote(row_key, safe='')}')"
            )
            lines.extend(
                [
                    f"--{changeset}",
                    "Content-Type: application/http",
                    "Content-Transfer-Encoding: binary",
                    f"Content-ID: {content_id}",
                    "",
                    f"DELETE {entity} HTTP/1.1",
                    "If-Match: *",
                    "Accept: application/json;odata=nometadata",
                    "DataServiceVersion: 3.0;",
                    "",
                ]
            )
        lines.extend([f"--{changeset}--", f"--{batch}--", ""])
        return batch, "\r\n".join(lines).encode("utf-8")

    def delete_reservations(self, partition: str, row_keys: Sequence[str]) -> int:
        """Delete exact settled rows in atomic same-partition batches of at most 100."""
        unique = sorted(set(row_keys))
        deleted = 0
        for offset in range(0, len(unique), 100):
            chunk = unique[offset : offset + 100]
            batch, body = self._batch_delete_body(
                self._endpoint, self._table, partition, chunk
            )
            response = self._request(
                "POST",
                f"{self._endpoint}/$batch",
                extra_headers={"Content-Type": f"multipart/mixed; boundary={batch}"},
                content=body,
            )
            response.raise_for_status()
            statuses = [int(value) for value in re.findall(r"HTTP/1\.1 (\d{3})", response.text)]
            if len(statuses) != len(chunk) or any(status != 204 for status in statuses):
                raise RuntimeError(
                    f"Table reservation delete transaction failed: statuses={statuses}"
                )
            deleted += len(chunk)
        return deleted


class LedgerSyncService:
    def __init__(
        self,
        repository: QueryRepository,
        store: LedgerStore,
    ) -> None:
        self._repository = repository
        self._store = store

    def _period_bounds(self, now: datetime) -> tuple[date, date, str]:
        start = period_start_for(now)
        end = (
            date(now.year + 1, 1, 1)
            if now.month == 12
            else date(now.year, now.month + 1, 1)
        )
        return start, end, f"{now.year:04d}-{now.month:02d}"

    def snapshot(self, now: datetime | None = None) -> list[LedgerPerson]:
        moment = now or datetime.now(UTC)
        period_start, period_end, _ = self._period_bounds(moment)
        rows = self._repository.budget_ledger_snapshot(period_start, period_end)
        return [
            LedgerPerson(
                user_id=str(row["user_id"]),
                department_id=str(row["department_id"]),
                token_limit=int(row["token_limit"]),
                confirmed_tokens=int(row["confirmed_tokens"]),
                enforce=str(row["mode"]) == "block",
            )
            for row in rows
        ]

    def model_access(
        self, user_ids: Sequence[str] | None = None
    ) -> list[LedgerModelAccess]:
        rows = self._repository.model_access_ledger_snapshot(user_ids)
        return [
            LedgerModelAccess(
                user_id=str(row["user_id"]),
                identifiers=tuple(
                    [*(row.get("model_uuids") or []), *(row.get("model_keys") or [])]
                ),
            )
            for row in rows
        ]

    def application_snapshot(
        self, now: datetime | None = None
    ) -> list[LedgerApplication]:
        moment = now or datetime.now(UTC)
        period_start, period_end, _ = self._period_bounds(moment)
        rows = self._repository.gateway_application_ledger_snapshot(
            period_start, period_end
        )
        return [
            LedgerApplication(
                application_id=str(row["application_id"]),
                token_limit=int(row["token_limit"]),
                tokens_per_minute=int(row["tokens_per_minute"]),
                confirmed_tokens=int(row["confirmed_tokens"]),
                enforce=bool(row["enforce"]),
            )
            for row in rows
            if str(row["status"]) != "retired"
        ]

    def application_model_access(self) -> list[LedgerApplicationModelAccess]:
        return [
            LedgerApplicationModelAccess(
                application_id=str(row["application_id"]),
                identifiers=tuple(
                    [*(row.get("model_uuids") or []), *(row.get("model_keys") or [])]
                ),
            )
            for row in self._repository.gateway_application_model_ledger_snapshot()
        ]

    def project_model_access(
        self, policies: Sequence[LedgerModelAccess], now: datetime | None = None
    ) -> int:
        """Write the M rows. Used by the timer and by the admin save path alike.

        The admin path calls this straight after the transaction commits so a revocation
        takes effect on the next request instead of at the next timer tick; the timer then
        only has to repair a write that failed.
        """
        moment = now or datetime.now(UTC)
        _, _, period = self._period_bounds(moment)
        written = 0
        for policy in policies:
            value = model_access_value(policy.identifiers)
            if len(value) > MODEL_PROPERTY_LIMIT:
                # Truncating would silently deny models the person is entitled to, which is
                # worse than the documented fail-open. Leave the row absent instead.
                logger.error(
                    "Model access for %s exceeds the ledger property limit; not projected",
                    policy.user_id,
                )
                continue
            self._store.upsert(
                partition_key(policy.user_id, period),
                MODEL_ACCESS_ROW_KEY,
                {"Configured": True, "Models": value},
            )
            written += 1
        return written

    def project_application_model_access(
        self,
        policies: Sequence[LedgerApplicationModelAccess],
        now: datetime | None = None,
    ) -> int:
        moment = now or datetime.now(UTC)
        _, _, period = self._period_bounds(moment)
        written = 0
        for policy in policies:
            value = model_access_value(policy.identifiers)
            if len(value) > MODEL_PROPERTY_LIMIT:
                logger.error(
                    "Application model access for %s exceeds the ledger property limit",
                    policy.application_id,
                )
                continue
            self._store.upsert(
                application_partition_key(policy.application_id, period),
                MODEL_ACCESS_ROW_KEY,
                {"Configured": True, "Models": value},
            )
            written += 1
        return written

    def project_application_mappings(self) -> int:
        written = 0
        for mapping in self._repository.gateway_application_ledger_mappings():
            self._store.upsert(
                application_map_partition_key(mapping["gateway_profile_id"]),
                str(mapping["apim_subscription_id"]),
                {
                    "ApplicationId": str(mapping["application_id"]),
                    "ApplicationSlug": str(mapping["application_slug"]),
                    "ApplicationName": str(mapping["application_name"]),
                    "ApplicationType": str(mapping["application_type"]),
                    "ApplicationStatus": str(mapping["application_status"]),
                    "SubscriptionState": str(mapping["subscription_state"]),
                    "ScopeExists": bool(mapping["scope_exists"]),
                },
            )
            written += 1
        return written

    def run(self, now: datetime | None = None) -> LedgerSyncOutcome:
        moment = now or datetime.now(UTC)
        current_start = period_start_for(moment)
        previous_moment = datetime.combine(
            current_start, time.min, tzinfo=UTC
        ) - timedelta(microseconds=1)

        people_count = 0
        settled_count = 0
        application_count = 0
        application_settled_count = 0
        # Current first: a request reserved just before midnight can land in the new
        # month's usage. Writing the current C before deleting the previous R preserves
        # conservative accounting if the Function fails between those two partitions.
        for period_moment in (moment, previous_moment):
            period_start, _, period = self._period_bounds(period_moment)

            # List first, then decide finality, then read usage. If telemetry arrives
            # between the finality query and the usage snapshot its R survives temporarily,
            # producing conservative double-counting. The reverse order could delete an R
            # whose usage was absent from C and would silently under-count.
            reservations: dict[str, list[LedgerReservation]] = {}
            for row in self._repository.budget_ledger_people(period_start):
                partition = partition_key(str(row["user_id"]), period)
                reservations[partition] = list(self._store.list_reservations(partition))
            settled = self._repository.settled_reservation_correlations(
                [
                    reservation.correlation_id
                    for rows in reservations.values()
                    for reservation in rows
                ]
            )

            people = self.snapshot(period_moment)
            people_count += len(people)
            for person in people:
                partition = partition_key(person.user_id, period)
                self._store.upsert(
                    partition,
                    "Q",
                    {"Limit": person.token_limit, "Enforce": person.enforce},
                )
                self._store.upsert(
                    partition,
                    "C",
                    {"ConfirmedUsed": person.confirmed_tokens},
                )
                settled_count += self._store.delete_reservations(
                    partition,
                    [
                        reservation.row_key
                        for reservation in reservations.get(partition, [])
                        if reservation.correlation_id in settled
                    ],
                )
            application_reservations: dict[str, list[LedgerReservation]] = {}
            for row in self._repository.gateway_application_ledger_applications(
                period_start
            ):
                if str(row["status"]) == "retired":
                    continue
                partition = application_partition_key(
                    str(row["application_id"]), period
                )
                application_reservations[partition] = list(
                    self._store.list_reservations(partition)
                )
            application_settled = self._repository.settled_reservation_correlations(
                [
                    reservation.correlation_id
                    for rows in application_reservations.values()
                    for reservation in rows
                ]
            )
            applications = self.application_snapshot(period_moment)
            application_count += len(applications)
            for application in applications:
                partition = application_partition_key(
                    application.application_id, period
                )
                self._store.upsert(
                    partition,
                    "Q",
                    {
                        "Limit": application.token_limit,
                        "TokensPerMinute": application.tokens_per_minute,
                        "Enforce": application.enforce,
                    },
                )
                self._store.upsert(
                    partition,
                    "C",
                    {"ConfirmedUsed": application.confirmed_tokens},
                )
                application_settled_count += self._store.delete_reservations(
                    partition,
                    [
                        reservation.row_key
                        for reservation in application_reservations.get(partition, [])
                        if reservation.correlation_id in application_settled
                    ],
                )
        # Model policies are projected independently of budgets: a person may be restricted
        # to a model set without ever being given an allowance, so iterating budgets alone
        # would leave their policy unenforced.
        policies = self.project_model_access(self.model_access(), moment)
        application_policies = self.project_application_model_access(
            self.application_model_access(), moment
        )
        application_mappings = self.project_application_mappings()
        return LedgerSyncOutcome(
            people=people_count,
            reservations_settled=settled_count,
            model_policies=policies,
            applications=application_count,
            application_reservations_settled=application_settled_count,
            application_model_policies=application_policies,
            application_mappings=application_mappings,
        )
