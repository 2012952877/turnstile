from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any, cast
from uuid import UUID

from psycopg.types.json import Jsonb

from ..domain.models import ModelIdentity


class PostgreSqlRegistryRepositoryMixin:
    _identities: dict[str, ModelIdentity] | None
    _identities_read_at: float
    _identity_lock: threading.Lock

    def _connection(self) -> AbstractContextManager[Any]:
        raise NotImplementedError

    def registry(self) -> dict[str, Sequence[dict[str, Any]]]:
        with self._connection() as connection:
            gateways = connection.execute(
                "SELECT * FROM gateway_profile ORDER BY name"
            ).fetchall()
            providers = connection.execute(
                """SELECT provider.*,
                                    COALESCE(metadata.brand_key, 'generic') AS brand_key
                     FROM model_provider provider
                     LEFT JOIN model_provider_metadata metadata
                         ON metadata.provider_id = provider.id
                     ORDER BY provider.name"""
            ).fetchall()
            runtimes = connection.execute(
                """SELECT runtime.*, provider.name AS provider_name,
                                    gateway.name AS gateway_name,
                                    COALESCE(metadata.brand_key, 'generic') AS brand_key
                     FROM model_runtime runtime
                     JOIN model_provider provider ON provider.id = runtime.provider_id
                     LEFT JOIN gateway_profile gateway ON gateway.id = runtime.gateway_profile_id
                     LEFT JOIN model_runtime_metadata metadata
                         ON metadata.runtime_id = runtime.id
                     ORDER BY runtime.is_default DESC, runtime.name"""
            ).fetchall()
            models = connection.execute(
                """SELECT model.*, provider.name AS provider_name,
                                    runtime.name AS runtime_name,
                                    COALESCE(metadata.family_key, 'generic') AS family_key,
                                    COALESCE(metadata.upstream_model_id, model.model_key)
                                        AS upstream_model_id,
                                    COALESCE(metadata.assignment_required, FALSE)
                                        AS assignment_required,
                                    metadata.publication_id
                     FROM managed_model model
                     JOIN model_provider provider ON provider.id = model.provider_id
                     JOIN model_runtime runtime ON runtime.id = model.runtime_id
                     LEFT JOIN managed_model_metadata metadata
                         ON metadata.model_id = model.id
                     ORDER BY model.is_default DESC, model.display_name"""
            ).fetchall()
        return {
            "gateways": cast(Sequence[dict[str, Any]], gateways),
            "providers": cast(Sequence[dict[str, Any]], providers),
            "runtimes": cast(Sequence[dict[str, Any]], runtimes),
            "models": cast(Sequence[dict[str, Any]], models),
        }

    def create_registry_item(self, kind: str, values: Mapping[str, Any]) -> dict[str, Any]:
        parameters = dict(values)
        if "config" in parameters:
            parameters["config"] = Jsonb(parameters["config"])
        with self._connection() as connection, connection.transaction():
            if kind == "gateway":
                if values["is_default"]:
                    connection.execute("UPDATE gateway_profile SET is_default = FALSE")
                row = connection.execute(
                    """INSERT INTO gateway_profile (
                               name, implementation, base_url, auth_type,
                               credential_ciphertext, credential_hint, enabled,
                               is_default, config
                           ) VALUES (%(name)s, %(implementation)s, %(base_url)s,
                               %(auth_type)s, %(credential_ciphertext)s,
                               %(credential_hint)s, %(enabled)s, %(is_default)s,
                               %(config)s) RETURNING *""",
                        parameters,
                ).fetchone()
            elif kind == "provider":
                row = connection.execute(
                    """INSERT INTO model_provider (
                               name, provider_kind, endpoint_url, auth_type,
                               credential_ciphertext, credential_hint, enabled, config
                           ) VALUES (%(name)s, %(provider_kind)s, %(endpoint_url)s,
                               %(auth_type)s, %(credential_ciphertext)s,
                               %(credential_hint)s, %(enabled)s, %(config)s)
                           RETURNING *""",
                        parameters,
                ).fetchone()
                connection.execute(
                    """INSERT INTO model_provider_metadata (provider_id, brand_key)
                       VALUES (%s, %s)""",
                    (row["id"], values.get("brand_key", "generic")),
                )
                row = {**row, "brand_key": values.get("brand_key", "generic")}
            elif kind == "runtime":
                if values["is_default"]:
                    connection.execute("UPDATE model_runtime SET is_default = FALSE")
                row = connection.execute(
                    """INSERT INTO model_runtime (
                               provider_id, gateway_profile_id, name, runtime_kind,
                               enabled, is_default, config, allowed_roles
                           ) VALUES (%(provider_id)s, %(gateway_profile_id)s,
                               %(name)s, %(runtime_kind)s, %(enabled)s,
                               %(is_default)s, %(config)s, %(allowed_roles)s)
                           RETURNING *""",
                        parameters,
                ).fetchone()
                connection.execute(
                    """INSERT INTO model_runtime_metadata (runtime_id, brand_key)
                       VALUES (%s, %s)""",
                    (row["id"], values.get("brand_key", "generic")),
                )
                row = {**row, "brand_key": values.get("brand_key", "generic")}
            elif kind == "model":
                if values["is_default"]:
                    connection.execute("UPDATE managed_model SET is_default = FALSE")
                    connection.execute("UPDATE model_runtime SET is_default = FALSE")
                    connection.execute(
                        "UPDATE model_runtime SET is_default = TRUE WHERE id = %s",
                        (parameters["runtime_id"],),
                    )
                row = connection.execute(
                    """INSERT INTO managed_model (
                               provider_id, runtime_id, model_key, display_name,
                               enabled, is_default, capabilities, context_window,
                               input_cost_per_million, output_cost_per_million,
                               cached_cost_per_million, cache_write_cost_per_million,
                               allowed_roles
                           ) VALUES (%(provider_id)s, %(runtime_id)s, %(model_key)s,
                               %(display_name)s, %(enabled)s, %(is_default)s,
                               %(capabilities)s, %(context_window)s,
                               %(input_cost_per_million)s, %(output_cost_per_million)s,
                               %(cached_cost_per_million)s, %(cache_write_cost_per_million)s,
                               %(allowed_roles)s) RETURNING *""",
                        parameters,
                ).fetchone()
                metadata = {
                    "family_key": values.get("family_key", "generic"),
                    "upstream_model_id": values.get("upstream_model_id")
                    or values["model_key"],
                    "assignment_required": values.get("assignment_required", False),
                    "publication_id": values.get("publication_id"),
                }
                connection.execute(
                    """INSERT INTO managed_model_metadata (
                           model_id, family_key, upstream_model_id,
                           assignment_required, publication_id
                       ) VALUES (%s, %s, %s, %s, %s)""",
                    (
                        row["id"],
                        metadata["family_key"],
                        metadata["upstream_model_id"],
                        metadata["assignment_required"],
                        metadata["publication_id"],
                    ),
                )
                row = {**row, **metadata}
            else:
                raise ValueError(f"Unsupported registry kind: {kind}")
        self._invalidate_identities()
        return cast(dict[str, Any], row)

    def create_connection(
        self,
        provider_id: UUID | None,
        provider_values: Mapping[str, Any] | None,
        runtime_values: Mapping[str, Any],
    ) -> dict[str, Any]:
        if (provider_id is None) == (provider_values is None):
            raise ValueError("select an existing provider or one provider template")
        runtime_parameters = dict(runtime_values)
        runtime_config = dict(runtime_parameters["config"])
        runtime_parameters["config"] = Jsonb(runtime_config)
        with self._connection() as connection, connection.transaction():
            project_endpoint = str(runtime_config.get("project_endpoint") or "").rstrip("/")
            if project_endpoint:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (
                        "model-connection:"
                        f"{runtime_values['gateway_profile_id']}:"
                        f"{project_endpoint.casefold()}",
                    ),
                )
                duplicate = connection.execute(
                    """SELECT id
                       FROM model_runtime
                       WHERE gateway_profile_id = %s
                         AND runtime_kind = 'foundry'
                         AND lower(rtrim(config->>'project_endpoint', '/')) = lower(%s)
                       LIMIT 1""",
                    (runtime_values["gateway_profile_id"], project_endpoint),
                ).fetchone()
                if duplicate is not None:
                    raise ValueError("This connection already exists")
            if provider_values is not None:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"model-provider:{provider_values['brand_key']}",),
                )
                provider = connection.execute(
                    """SELECT provider.*
                       FROM model_provider provider
                       JOIN model_provider_metadata metadata
                         ON metadata.provider_id = provider.id
                       WHERE metadata.brand_key = %s AND provider.enabled
                       ORDER BY provider.created_at
                       LIMIT 1""",
                    (provider_values["brand_key"],),
                ).fetchone()
                if provider is None:
                    parameters = dict(provider_values)
                    parameters["config"] = Jsonb(parameters["config"])
                    provider = connection.execute(
                        """INSERT INTO model_provider (
                                   name, provider_kind, endpoint_url, auth_type,
                                   credential_ciphertext, credential_hint, enabled, config
                               ) VALUES (%(name)s, %(provider_kind)s, %(endpoint_url)s,
                                   %(auth_type)s, %(credential_ciphertext)s,
                                   %(credential_hint)s, %(enabled)s, %(config)s)
                               RETURNING *""",
                        parameters,
                    ).fetchone()
                    connection.execute(
                        """INSERT INTO model_provider_metadata (provider_id, brand_key)
                           VALUES (%s, %s)""",
                        (provider["id"], provider_values["brand_key"]),
                    )
                provider_id = provider["id"]
            runtime_parameters["provider_id"] = provider_id
            row = connection.execute(
                """INSERT INTO model_runtime (
                           provider_id, gateway_profile_id, name, runtime_kind,
                           enabled, is_default, config, allowed_roles
                       ) VALUES (%(provider_id)s, %(gateway_profile_id)s,
                           %(name)s, %(runtime_kind)s, %(enabled)s,
                           %(is_default)s, %(config)s, %(allowed_roles)s)
                       RETURNING *""",
                runtime_parameters,
            ).fetchone()
            connection.execute(
                """INSERT INTO model_runtime_metadata (runtime_id, brand_key)
                   VALUES (%s, %s)""",
                (row["id"], runtime_values.get("brand_key", "generic")),
            )
        self._invalidate_identities()
        return cast(
            dict[str, Any],
            {**row, "brand_key": runtime_values.get("brand_key", "generic")},
        )

    def delete_runtime_if_empty(self, runtime_id: UUID) -> bool:
        with self._connection() as connection, connection.transaction():
            row = connection.execute(
                """DELETE FROM model_runtime runtime
                   WHERE runtime.id = %s
                     AND NOT EXISTS (
                         SELECT 1 FROM managed_model model
                         WHERE model.runtime_id = runtime.id
                     )
                   RETURNING runtime.id""",
                (runtime_id,),
            ).fetchone()
        if row is not None:
            self._invalidate_identities()
        return row is not None

    def delete_gateway_if_unused(self, gateway_id: UUID) -> bool:
        with self._connection() as connection, connection.transaction():
            row = connection.execute(
                """DELETE FROM gateway_profile gateway
                   WHERE gateway.id = %s
                     AND NOT gateway.is_default
                     AND NOT EXISTS (
                         SELECT 1 FROM model_runtime runtime
                         WHERE runtime.gateway_profile_id = gateway.id
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM gateway_publication publication
                         WHERE publication.gateway_profile_id = gateway.id
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM effective_gateway_release release
                         WHERE release.gateway_profile_id = gateway.id
                     )
                   RETURNING gateway.id""",
                (gateway_id,),
            ).fetchone()
        return row is not None

    def _invalidate_identities(self) -> None:
        """Drop the cached identity map after a registry write.

        Without this an administrator could rename a model and still see the old name in a
        trace for a full cache window, which reads as the write having failed.
        """
        with self._identity_lock:
            self._identities = None
            self._identities_read_at = 0.0

    def update_registry_item(
        self, kind: str, item_id: UUID, values: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        table = {
            "gateway": "gateway_profile",
            "provider": "model_provider",
            "runtime": "model_runtime",
            "model": "managed_model",
        }.get(kind)
        if table is None:
            raise ValueError(f"Unsupported registry kind: {kind}")
        allowed = {
            "gateway": [
                "name",
                "implementation",
                "base_url",
                "auth_type",
                "credential_ciphertext",
                "credential_hint",
                "enabled",
                "is_default",
                "config",
            ],
            "provider": [
                "name",
                "provider_kind",
                "endpoint_url",
                "auth_type",
                "credential_ciphertext",
                "credential_hint",
                "enabled",
                "config",
            ],
            "runtime": [
                "provider_id",
                "gateway_profile_id",
                "name",
                "runtime_kind",
                "enabled",
                "is_default",
                "config",
                "allowed_roles",
            ],
            "model": [
                "provider_id",
                "runtime_id",
                "model_key",
                "display_name",
                "enabled",
                "is_default",
                "capabilities",
                "context_window",
                "input_cost_per_million",
                "output_cost_per_million",
                "cached_cost_per_million",
                "cache_write_cost_per_million",
                "allowed_roles",
            ],
        }[kind]
        assignments = [f"{column} = %({column})s" for column in allowed if column in values]
        metadata_fields = {
            "provider": {"brand_key"},
            "runtime": {"brand_key"},
            "model": {"family_key", "upstream_model_id", "assignment_required"},
        }.get(kind, set())
        metadata_values = {key: values[key] for key in metadata_fields if key in values}
        if not assignments and not metadata_values:
            return None
        parameters = dict(values)
        if "config" in parameters:
            parameters["config"] = Jsonb(parameters["config"])
        parameters["id"] = item_id
        with self._connection() as connection, connection.transaction():
            if kind == "model":
                removing = connection.execute(
                    """SELECT id FROM gateway_publication
                       WHERE publication_kind = 'model_remove'
                         AND status IN (
                           'queued', 'validating', 'provisioning', 'building_revision',
                           'verifying', 'promoting', 'rolling_back'
                         )
                         AND desired_spec #>> '{removed_models,0,model_id}' = %s""",
                    (str(item_id),),
                ).fetchone()
                if removing is not None:
                    raise ValueError("Model deletion is already in progress")
            if values.get("is_default") is True:
                connection.execute(f"UPDATE {table} SET is_default = FALSE")
                if kind == "runtime":
                    connection.execute("UPDATE managed_model SET is_default = FALSE")
                    connection.execute(
                        """UPDATE managed_model SET is_default = TRUE
                           WHERE id = (
                               SELECT id FROM managed_model
                               WHERE runtime_id = %s AND enabled
                               ORDER BY created_at LIMIT 1
                           )""",
                        (item_id,),
                    )
                elif kind == "model":
                    connection.execute("UPDATE model_runtime SET is_default = FALSE")
                    connection.execute(
                        "UPDATE model_runtime SET is_default = TRUE WHERE id = %s",
                        (parameters["runtime_id"],),
                    )
            if assignments:
                row = connection.execute(
                    f"UPDATE {table} SET {', '.join(assignments)}, updated_at = now() "
                    "WHERE id = %(id)s RETURNING *",
                    parameters,
                ).fetchone()
            else:
                row = connection.execute(
                    f"SELECT * FROM {table} WHERE id = %s", (item_id,)
                ).fetchone()
            if row is None:
                return None
            if kind == "provider" and metadata_values:
                brand_key = metadata_values.get("brand_key", "generic")
                connection.execute(
                    """INSERT INTO model_provider_metadata (provider_id, brand_key)
                       VALUES (%s, %s)
                       ON CONFLICT (provider_id) DO UPDATE SET
                         brand_key = EXCLUDED.brand_key""",
                    (item_id, brand_key),
                )
                row = {**row, "brand_key": brand_key}
            elif kind == "runtime" and metadata_values:
                brand_key = metadata_values.get("brand_key", "generic")
                connection.execute(
                    """INSERT INTO model_runtime_metadata (runtime_id, brand_key)
                       VALUES (%s, %s)
                       ON CONFLICT (runtime_id) DO UPDATE SET
                         brand_key = EXCLUDED.brand_key""",
                    (item_id, brand_key),
                )
                row = {**row, "brand_key": brand_key}
            elif kind == "model" and metadata_values:
                metadata = connection.execute(
                    """SELECT * FROM managed_model_metadata WHERE model_id = %s""",
                    (item_id,),
                ).fetchone()
                family_key = metadata_values.get(
                    "family_key", metadata["family_key"] if metadata else "generic"
                )
                upstream_model_id = metadata_values.get(
                    "upstream_model_id",
                    metadata["upstream_model_id"] if metadata else row["model_key"],
                )
                assignment_required = metadata_values.get(
                    "assignment_required",
                    metadata["assignment_required"] if metadata else False,
                )
                connection.execute(
                    """INSERT INTO managed_model_metadata (
                           model_id, family_key, upstream_model_id, assignment_required
                       ) VALUES (%s, %s, %s, %s)
                       ON CONFLICT (model_id) DO UPDATE SET
                         family_key = EXCLUDED.family_key,
                         upstream_model_id = EXCLUDED.upstream_model_id,
                         assignment_required = EXCLUDED.assignment_required""",
                    (item_id, family_key, upstream_model_id, assignment_required),
                )
                row = {
                    **row,
                    "family_key": family_key,
                    "upstream_model_id": upstream_model_id,
                    "assignment_required": assignment_required,
                    "publication_id": metadata["publication_id"] if metadata else None,
                }
        self._invalidate_identities()
        return cast(dict[str, Any] | None, row)
