from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from backend.services.auth_service import hash_password
from scripts.stage_deployment import REPOSITORY_ROOT, stage_deployment, validate_source_snapshot

JsonObject = dict[str, Any]
PasswordReader = Callable[[str], str]
SECRET_PARAMETER_NAMES = {
    "postgresAdministratorPassword",
    "credentialEncryptionKey",
    "managementApiKey",
    "apimSubscriptionKey",
    "apimProbeSubscriptionKey",
    "bootstrapOwnerPasswordHash",
}
STATE_SECRET_NAMES = SECRET_PARAMETER_NAMES | {"observerAdapterSharedKey"}
EXPECTED_FUNCTIONS = {
    "telemetryFunctionName": {
        "telemetry_health",
        "process_usage_events",
        "reconcile_stream_usage",
        "sync_budget_ledger",
    },
    "controlPlaneFunctionName": {
        "publish_gateway_changes",
        "process_gateway_release_operations",
    },
}
FIXED_ZIP_TIMESTAMP = (2020, 1, 1, 0, 0, 0)
POSTGRES_VERSION = "16"
POSTGRES_AVAILABILITY_ZONE = "1"
DEFAULT_POSTGRES_SKU_NAME = "Standard_B1ms"
DEFAULT_POSTGRES_TIER = "Burstable"


class DeploymentError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeploymentInputs:
    subscription: str
    parameters_path: Path
    parameters: JsonObject
    resource_group_name: str
    resource_prefix: str
    location: str
    postgres_location: str
    postgres_sku_name: str
    postgres_tier: str
    owner_email: str
    state_path: Path

    @classmethod
    def load(
        cls,
        subscription: str,
        parameters_path: Path,
        state_path: Path | None = None,
    ) -> DeploymentInputs:
        try:
            document = json.loads(parameters_path.read_text(encoding="utf-8"))
            raw_parameters = document["parameters"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise DeploymentError(f"Invalid ARM parameter file: {parameters_path}") from error
        if not isinstance(raw_parameters, dict):
            raise DeploymentError("The ARM parameter file must contain a parameters object")
        parameters: JsonObject = {}
        for name, entry in raw_parameters.items():
            if not isinstance(entry, dict) or "value" not in entry:
                raise DeploymentError(f"Parameter {name} must contain a value")
            parameters[str(name)] = entry["value"]
        leaked = sorted(name for name in SECRET_PARAMETER_NAMES if name in parameters)
        if leaked:
            raise DeploymentError(
                "Keep secure parameters out of the public parameter file: " + ", ".join(leaked)
            )
        resource_prefix = _required_string(parameters, "resourcePrefix")
        location = _required_string(parameters, "location")
        postgres_location = _string_parameter(parameters, "postgresLocation", location)
        postgres_sku_name = _string_parameter(
            parameters, "postgresSkuName", DEFAULT_POSTGRES_SKU_NAME
        )
        postgres_tier = _string_parameter(
            parameters, "postgresTier", DEFAULT_POSTGRES_TIER
        )
        owner_email = _required_string(parameters, "bootstrapOwnerEmail").strip().lower()
        if "@" not in owner_email:
            raise DeploymentError("bootstrapOwnerEmail must be an email address")
        resource_group_name = str(
            parameters.get("resourceGroupName") or f"{resource_prefix}-platform"
        )
        resolved_state = state_path or (
            REPOSITORY_ROOT / ".turnstile" / "deployments" / f"{resource_group_name}.json"
        )
        return cls(
            subscription=subscription,
            parameters_path=parameters_path,
            parameters=parameters,
            resource_group_name=resource_group_name,
            resource_prefix=resource_prefix,
            location=location,
            postgres_location=postgres_location,
            postgres_sku_name=postgres_sku_name,
            postgres_tier=postgres_tier,
            owner_email=owner_email,
            state_path=resolved_state,
        )


@dataclass(frozen=True)
class SecretMaterial:
    values: dict[str, str]
    owner_password: str | None


@dataclass(frozen=True)
class ExistingCore:
    apim_name: str
    apim_principal_id: str
    apim_gateway_url: str

    @classmethod
    def from_outputs(cls, outputs: Mapping[str, Any]) -> ExistingCore:
        return cls(
            apim_name=_output_string(outputs, "apimName"),
            apim_principal_id=_output_string(outputs, "apimPrincipalId"),
            apim_gateway_url=_output_string(outputs, "apimGatewayUrl"),
        )


class CommandRunner:
    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        capture: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        print("$ " + " ".join(command))
        return subprocess.run(
            list(command),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            check=True,
            capture_output=capture,
            text=True,
        )

    def run_json(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> JsonObject:
        result = self.run(command, cwd=cwd, env=env, capture=True)
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise DeploymentError(f"Command did not return JSON: {' '.join(command)}") from error
        if not isinstance(value, dict):
            raise DeploymentError(f"Command returned a non-object JSON value: {' '.join(command)}")
        return value


def _required_string(parameters: Mapping[str, Any], name: str) -> str:
    value = parameters.get(name)
    if not isinstance(value, str) or not value.strip():
        raise DeploymentError(f"Parameter {name} is required")
    return value.strip()


def _string_parameter(parameters: Mapping[str, Any], name: str, default: str) -> str:
    value = parameters.get(name, default)
    if not isinstance(value, str) or not value.strip():
        raise DeploymentError(f"Parameter {name} must be a non-empty string")
    return value.strip()


def _read_owner_password(read_password: PasswordReader) -> str:
    password = read_password("Initial Owner password: ")
    if password != read_password("Repeat Initial Owner password: "):
        raise DeploymentError("Initial Owner passwords do not match")
    if len(password) < 12:
        raise DeploymentError("Initial Owner password must be at least 12 characters")
    return password


def owner_credentials_password(path: Path, expected_email: str) -> str:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise DeploymentError(f"Owner credentials permissions must be 0600: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DeploymentError(f"Invalid Owner credentials file: {path}") from error
    if not isinstance(document, dict) or set(document) != {"email", "password"}:
        raise DeploymentError("Owner credentials must contain only email and password")
    email = document.get("email")
    password = document.get("password")
    if not isinstance(email, str) or email.strip().lower() != expected_email:
        raise DeploymentError("Owner credentials email does not match bootstrapOwnerEmail")
    if not isinstance(password, str) or len(password) < 12:
        raise DeploymentError("Initial Owner password must be at least 12 characters")
    return password


def load_or_create_secret_material(
    inputs: DeploymentInputs,
    *,
    read_password: PasswordReader = getpass.getpass,
    require_owner_password: bool,
) -> SecretMaterial:
    state_path = inputs.state_path
    owner_password: str | None = None
    if state_path.exists():
        mode = stat.S_IMODE(state_path.stat().st_mode)
        if mode & 0o077:
            raise DeploymentError(f"Secret state permissions must be 0600: {state_path}")
        try:
            document = json.loads(state_path.read_text(encoding="utf-8"))
            values = document["parameters"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise DeploymentError(f"Invalid secret state: {state_path}") from error
        if not isinstance(values, dict) or any(
            not isinstance(values.get(name), str) or not values[name]
            for name in STATE_SECRET_NAMES
        ):
            raise DeploymentError(f"Secret state is incomplete: {state_path}")
        if require_owner_password:
            owner_password = read_password("Initial Owner password for verification: ")
        return SecretMaterial(dict(values), owner_password)

    owner_password = _read_owner_password(read_password)
    values = {
        "postgresAdministratorPassword": secrets.token_urlsafe(32),
        "credentialEncryptionKey": Fernet.generate_key().decode("ascii"),
        "managementApiKey": secrets.token_urlsafe(32),
        "apimSubscriptionKey": secrets.token_hex(32),
        "apimProbeSubscriptionKey": secrets.token_hex(32),
        "bootstrapOwnerPasswordHash": hash_password(owner_password),
        "observerAdapterSharedKey": secrets.token_urlsafe(32),
    }
    state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"version": 1, "parameters": values}, handle, indent=2)
        handle.write("\n")
    print(f"Created secret state: {state_path}")
    return SecretMaterial(values, owner_password if require_owner_password else None)


def deployment_parameters(
    inputs: DeploymentInputs,
    secrets_: SecretMaterial,
    *,
    observer: Mapping[str, str] | None = None,
    existing_core: ExistingCore | None = None,
) -> JsonObject:
    values = dict(inputs.parameters)
    values.update({name: secrets_.values[name] for name in SECRET_PARAMETER_NAMES})
    values.update(
        bootstrapOwnerEmail=inputs.owner_email,
        provisionControlPlane=True,
        gatewayApplicationKeyManagementEnabled=True,
        controlPlaneEnabled=observer is not None,
        gatewayReleaseWorkerEnabled=observer is not None,
        provisionApimService=existing_core is None,
        provisionPostgres=existing_core is None,
        deployApimBootstrap=existing_core is None,
        existingApimName=existing_core.apim_name if existing_core else "",
        existingApimPrincipalId=(
            existing_core.apim_principal_id if existing_core else ""
        ),
        existingApimGatewayUrl=existing_core.apim_gateway_url if existing_core else "",
    )
    if observer is None:
        values["apimUsageObserver"] = {
            "mode": "disabled",
            "url": "",
            "keyNamedValue": "",
            "legacyFoundryUpstreamHost": "",
            "legacyFoundryUpstreamBasePath": "",
            "legacyDatabricksUpstreamHost": "",
            "legacyDatabricksUpstreamBasePath": "",
        }
    else:
        values["apimUsageObserver"] = {
            "mode": "enabled",
            "url": observer["webAppUrl"],
            "keyNamedValue": observer["adapterKeyNamedValueName"],
            "legacyFoundryUpstreamHost": "unused.invalid",
            "legacyFoundryUpstreamBasePath": "/",
            "legacyDatabricksUpstreamHost": "unused.invalid",
            "legacyDatabricksUpstreamBasePath": "/",
        }
    return _arm_parameter_document(values)


def _arm_parameter_document(values: Mapping[str, Any]) -> JsonObject:
    return {
        "$schema": (
            "https://schema.management.azure.com/schemas/"
            "2019-04-01/deploymentParameters.json#"
        ),
        "contentVersion": "1.0.0.0",
        "parameters": {name: {"value": value} for name, value in values.items()},
    }


@contextmanager
def temporary_parameter_file(document: Mapping[str, Any], directory: Path) -> Iterator[Path]:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, raw_path = tempfile.mkstemp(prefix="parameters-", suffix=".json", dir=directory)
    path = Path(raw_path)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
            handle.write("\n")
        yield path
    finally:
        path.unlink(missing_ok=True)


def require_prerequisites(runner: CommandRunner, subscription: str) -> None:
    missing = [name for name in ("az", "git", "uv", "npm") if shutil.which(name) is None]
    if missing:
        raise DeploymentError("Missing required tools: " + ", ".join(missing))
    runner.run_json(
        ["az", "account", "show", "--subscription", subscription, "--output", "json"]
    )
    runner.run(["az", "bicep", "version"], capture=True)


def validate_postgres_capabilities(
    runner: CommandRunner, inputs: DeploymentInputs
) -> None:
    if inputs.parameters.get("provisionPostgres", True) is False:
        return
    capabilities = runner.run_json(
        [
            "az",
            "postgres",
            "flexible-server",
            "list-skus",
            "--subscription",
            inputs.subscription,
            "--location",
            inputs.postgres_location,
            "--query",
            (
                "[0].{reason:reason,versions:supportedServerVersions[].name,"
                "editions:supportedServerEditions[].{name:name,"
                "skus:supportedServerSkus[].{name:name,zones:supportedZones}}}"
            ),
            "--output",
            "json",
        ]
    )
    versions = capabilities.get("versions")
    if not isinstance(versions, list) or POSTGRES_VERSION not in versions:
        reason = capabilities.get("reason")
        detail = f": {reason}" if isinstance(reason, str) and reason else ""
        raise DeploymentError(
            f"PostgreSQL {POSTGRES_VERSION} is unavailable in "
            f"{inputs.postgres_location}{detail}"
        )

    combination_supported = False
    editions = capabilities.get("editions")
    if isinstance(editions, list):
        for edition in editions:
            if not isinstance(edition, dict) or edition.get("name") != inputs.postgres_tier:
                continue
            skus = edition.get("skus")
            if not isinstance(skus, list):
                continue
            for sku in skus:
                if not isinstance(sku, dict) or sku.get("name") != inputs.postgres_sku_name:
                    continue
                zones = sku.get("zones")
                combination_supported = (
                    isinstance(zones, list) and POSTGRES_AVAILABILITY_ZONE in zones
                )
                if combination_supported:
                    break
    if not combination_supported:
        raise DeploymentError(
            f"PostgreSQL {inputs.postgres_tier}/{inputs.postgres_sku_name} in zone "
            f"{POSTGRES_AVAILABILITY_ZONE} is unavailable in {inputs.postgres_location}"
        )


def _deployment_command(
    action: str,
    inputs: DeploymentInputs,
    template: Path,
    parameter_file: Path,
    deployment_name: str,
) -> list[str]:
    command = [
        "az",
        "deployment",
        "sub",
        action,
        "--subscription",
        inputs.subscription,
        "--name",
        deployment_name,
        "--location",
        inputs.location,
        "--template-file",
        str(template),
        "--parameters",
        f"@{parameter_file}",
        "--output",
        "json",
    ]
    if action == "what-if":
        command.extend(["--no-pretty-print", "--result-format", "FullResourcePayloads"])
    return command


def _resource_group_deployment_command(
    action: str,
    inputs: DeploymentInputs,
    template: Path,
    parameter_file: Path,
    deployment_name: str,
) -> list[str]:
    command = [
        "az",
        "deployment",
        "group",
        action,
        "--subscription",
        inputs.subscription,
        "--resource-group",
        inputs.resource_group_name,
        "--name",
        deployment_name,
        "--template-file",
        str(template),
        "--parameters",
        f"@{parameter_file}",
        "--output",
        "json",
    ]
    if action == "what-if":
        command.extend(["--no-pretty-print", "--result-format", "FullResourcePayloads"])
    return command


def _what_if_counts(result: Mapping[str, Any]) -> Counter[str]:
    changes = result.get("changes")
    if not isinstance(changes, list):
        properties = result.get("properties")
        changes = properties.get("changes", []) if isinstance(properties, dict) else []
    if not isinstance(changes, list):
        raise DeploymentError("Azure what-if returned an invalid changes value")
    counts = Counter(
        str(change.get("changeType", "Unknown"))
        for change in changes
        if isinstance(change, dict)
    )
    print("What-if: " + ", ".join(f"{name}={count}" for name, count in sorted(counts.items())))
    if counts["Delete"]:
        raise DeploymentError("What-if contains Delete changes; deployment stopped")
    return counts


def what_if(
    runner: CommandRunner,
    inputs: DeploymentInputs,
    template: Path,
    parameters: Mapping[str, Any],
    deployment_name: str,
) -> Counter[str]:
    with temporary_parameter_file(parameters, inputs.state_path.parent) as parameter_file:
        result = runner.run_json(
            _deployment_command(
                "what-if", inputs, template, parameter_file, deployment_name
            )
        )
    return _what_if_counts(result)


def what_if_resource_group(
    runner: CommandRunner,
    inputs: DeploymentInputs,
    template: Path,
    parameters: Mapping[str, Any],
    deployment_name: str,
) -> Counter[str]:
    with temporary_parameter_file(parameters, inputs.state_path.parent) as parameter_file:
        result = runner.run_json(
            _resource_group_deployment_command(
                "what-if", inputs, template, parameter_file, deployment_name
            )
        )
    return _what_if_counts(result)


def deploy_template(
    runner: CommandRunner,
    inputs: DeploymentInputs,
    template: Path,
    parameters: Mapping[str, Any],
    deployment_name: str,
) -> JsonObject:
    with temporary_parameter_file(parameters, inputs.state_path.parent) as parameter_file:
        return runner.run_json(
            _deployment_command("create", inputs, template, parameter_file, deployment_name)
        )


def deploy_resource_group_template(
    runner: CommandRunner,
    inputs: DeploymentInputs,
    template: Path,
    parameters: Mapping[str, Any],
    deployment_name: str,
) -> JsonObject:
    with temporary_parameter_file(parameters, inputs.state_path.parent) as parameter_file:
        return runner.run_json(
            _resource_group_deployment_command(
                "create", inputs, template, parameter_file, deployment_name
            )
        )


def deployment_outputs(result: Mapping[str, Any]) -> dict[str, Any]:
    properties = result.get("properties")
    raw_outputs = properties.get("outputs") if isinstance(properties, dict) else None
    if not isinstance(raw_outputs, dict):
        raise DeploymentError("Azure deployment returned no outputs")
    outputs: dict[str, Any] = {}
    for name, entry in raw_outputs.items():
        if isinstance(entry, dict) and "value" in entry:
            outputs[str(name)] = entry["value"]
    return outputs


def source_version(runner: CommandRunner) -> str:
    result = runner.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=REPOSITORY_ROOT,
        capture=True,
    )
    return result.stdout.strip()


def deterministic_zip(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            relative = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(relative, FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (path.stat().st_mode & 0xFFFF) << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)


def linux_dependency_command(staged: Path) -> list[str]:
    target = staged / ".python_packages" / "lib" / "site-packages"
    target.mkdir(parents=True)
    return [
        "uv",
        "pip",
        "install",
        "--python-platform",
        "x86_64-manylinux_2_17",
        "--python-version",
        "3.11",
        "--target",
        str(target),
        "--requirements",
        str(staged / "requirements.txt"),
    ]


def pip_linux_dependency_command(staged: Path, pip: str) -> list[str]:
    target = staged / ".python_packages" / "lib" / "site-packages"
    target.mkdir(parents=True, exist_ok=True)
    return [
        pip,
        "install",
        "--disable-pip-version-check",
        "--only-binary=:all:",
        "--platform",
        "manylinux2014_x86_64",
        "--implementation",
        "cp",
        "--python-version",
        "3.11",
        "--target",
        str(target),
        "--requirement",
        str(staged / "requirements.txt"),
    ]


def _install_linux_dependencies(runner: CommandRunner, staged: Path) -> None:
    try:
        runner.run(linux_dependency_command(staged), cwd=REPOSITORY_ROOT)
    except subprocess.CalledProcessError:
        pip = shutil.which("pip3")
        if pip is None:
            raise DeploymentError(
                "uv could not install Linux dependencies and pip3 is unavailable for fallback"
            ) from None
        print("uv dependency installation failed; retrying with pip.")
        runner.run(
            pip_linux_dependency_command(staged, pip),
            cwd=REPOSITORY_ROOT,
        )


def build_packages(
    runner: CommandRunner,
    inputs: DeploymentInputs,
    version: str,
) -> dict[str, Path]:
    environment = dict(os.environ)
    environment["VITE_ENTRA_CLIENT_ID"] = str(inputs.parameters.get("entraClientId") or "")
    runner.run(["npm", "--prefix", "frontend", "ci"], cwd=REPOSITORY_ROOT)
    runner.run(
        ["npm", "--prefix", "frontend", "run", "build"],
        cwd=REPOSITORY_ROOT,
        env=environment,
    )
    build_root = inputs.state_path.parent / "build" / version
    if build_root.exists():
        shutil.rmtree(build_root)
    build_root.mkdir(parents=True)
    packages: dict[str, Path] = {}
    for target in ("api", "telemetry", "control-plane"):
        staged = build_root / target
        stage_deployment(target, staged)
        _install_linux_dependencies(runner, staged)
        archive = build_root / f"{target}.zip"
        deterministic_zip(staged, archive)
        packages[target] = archive
    return packages


def deploy_packages(
    runner: CommandRunner,
    inputs: DeploymentInputs,
    outputs: Mapping[str, Any],
    packages: Mapping[str, Path],
) -> None:
    resource_group = _output_string(outputs, "resourceGroupName")
    api_name = _output_string(outputs, "apiName")
    runner.run(
        [
            "az",
            "webapp",
            "deploy",
            "--subscription",
            inputs.subscription,
            "--resource-group",
            resource_group,
            "--name",
            api_name,
            "--src-path",
            str(packages["api"]),
            "--type",
            "zip",
            "--clean",
            "true",
            "--restart",
            "true",
            "--track-status",
            "false",
            "--output",
            "json",
        ]
    )
    wait_for_health(_output_string(outputs, "apiUrl"), timeout_seconds=1800)
    for output_name, package_name in (
        ("telemetryFunctionName", "telemetry"),
        ("controlPlaneFunctionName", "control-plane"),
    ):
        function_name = _output_string(outputs, output_name)
        runner.run(
            [
                "az",
                "functionapp",
                "deployment",
                "source",
                "config-zip",
                "--subscription",
                inputs.subscription,
                "--resource-group",
                resource_group,
                "--name",
                function_name,
                "--src",
                str(packages[package_name]),
                "--build-remote",
                "false",
                "--output",
                "json",
            ]
        )


def _output_string(outputs: Mapping[str, Any], name: str) -> str:
    value = outputs.get(name)
    if not isinstance(value, str) or not value:
        raise DeploymentError(f"Deployment output {name} is missing")
    return value


def observer_names(inputs: DeploymentInputs) -> tuple[str, str]:
    digest = hashlib.sha256(
        f"{inputs.subscription}:{inputs.resource_group_name}".encode()
    ).hexdigest()[:12]
    compact_prefix = re.sub(r"[^a-z0-9]", "", inputs.resource_prefix.lower())
    return f"cr{compact_prefix}{digest}"[:50], f"obs-{compact_prefix}-{digest}"[:60]


def observer_parameters(
    inputs: DeploymentInputs,
    platform_outputs: Mapping[str, Any],
    secrets_: SecretMaterial,
    version: str,
    existing_observer: Mapping[str, Any] | None = None,
) -> JsonObject:
    if existing_observer is None:
        acr_name, web_app_name = observer_names(inputs)
    else:
        acr_name = _output_string(existing_observer, "acrName")
        web_app_name = _output_string(existing_observer, "webAppName")
    observer_plan_name = (
        _output_string(existing_observer, "observerAppServicePlanName")
        if existing_observer is not None
        else _output_string(platform_outputs, "appServicePlanName")
    )
    return _arm_parameter_document(
        {
            "resourceGroupName": _output_string(platform_outputs, "resourceGroupName"),
            "apimResourceGroupName": _output_string(
                platform_outputs, "resourceGroupName"
            ),
            "location": inputs.location,
            "appServicePlanName": observer_plan_name,
            "webAppName": web_app_name,
            "acrName": acr_name,
            "provisionAcr": existing_observer is None,
            "imageTag": version,
            "eventHubNamespaceName": _output_string(
                platform_outputs, "eventHubNamespaceName"
            ),
            "eventHubName": "token-usage",
            "apimName": _output_string(platform_outputs, "apimName"),
            "adapterSharedKey": secrets_.values["observerAdapterSharedKey"],
        }
    )


def runtime_release_parameters(
    secrets_: SecretMaterial,
    platform_outputs: Mapping[str, Any],
    observer_outputs: Mapping[str, Any],
    current_api_settings: Mapping[str, str],
    current_control_plane_settings: Mapping[str, str],
) -> JsonObject:
    return _arm_parameter_document(
        {
            "apiName": _output_string(platform_outputs, "apiName"),
            "controlPlaneFunctionName": _output_string(
                platform_outputs, "controlPlaneFunctionName"
            ),
            "apimGatewayUrl": _output_string(platform_outputs, "gatewayApiPath"),
            "apimSubscriptionKey": secrets_.values["apimSubscriptionKey"],
            "usageObserverUrl": _output_string(observer_outputs, "webAppUrl"),
            "usageObserverKeyNamedValue": _output_string(
                observer_outputs, "adapterKeyNamedValueName"
            ),
            "publicationWorkerEnabled": True,
            "releaseWorkerEnabled": True,
            "currentApiSettings": dict(current_api_settings),
            "currentControlPlaneSettings": dict(current_control_plane_settings),
        }
    )


def current_app_settings(
    runner: CommandRunner,
    inputs: DeploymentInputs,
    app_name: str,
) -> dict[str, str]:
    resource_id = (
        f"/subscriptions/{inputs.subscription}/resourceGroups/{inputs.resource_group_name}"
        f"/providers/Microsoft.Web/sites/{app_name}/config/appsettings/list"
    )
    result = runner.run_json(
        [
            "az",
            "rest",
            "--method",
            "post",
            "--url",
            f"https://management.azure.com{resource_id}?api-version=2024-11-01",
            "--output",
            "json",
        ]
    )
    properties = result.get("properties")
    if not isinstance(properties, dict) or any(
        not isinstance(name, str) or not isinstance(value, str)
        for name, value in properties.items()
    ):
        raise DeploymentError(f"App Service returned invalid settings for {app_name}")
    return dict(properties)


def build_and_start_observer(
    runner: CommandRunner,
    inputs: DeploymentInputs,
    observer_outputs: Mapping[str, Any],
    version: str,
) -> None:
    acr_name = _output_string(observer_outputs, "acrName")
    image = _output_string(observer_outputs, "image")
    image_repository_and_tag = image.split("/", maxsplit=1)[1]
    runner.run(
        [
            "az",
            "acr",
            "build",
            "--subscription",
            inputs.subscription,
            "--registry",
            acr_name,
            "--image",
            image_repository_and_tag,
            "--file",
            "Dockerfile",
            ".",
        ],
        cwd=REPOSITORY_ROOT / "infra" / "envoy-cache-adapter",
    )
    web_app_name = _output_string(observer_outputs, "webAppName")
    runner.run(
        [
            "az",
            "webapp",
            "restart",
            "--subscription",
            inputs.subscription,
            "--resource-group",
            inputs.resource_group_name,
            "--name",
            web_app_name,
        ]
    )
    print(f"Observer image ready: {image_repository_and_tag} ({version})")


def _open_without_proxy(request: urllib.request.Request, timeout: float) -> bytes:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        body = response.read()
    if not isinstance(body, bytes):
        raise DeploymentError("HTTP response body was not bytes")
    return body


def wait_for_health(api_url: str, timeout_seconds: int = 180) -> str:
    deadline = time.monotonic() + timeout_seconds
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            body = _open_without_proxy(
                urllib.request.Request(f"{api_url.rstrip('/')}/health"), 15
            ).decode("utf-8")
            return body
        except (OSError, urllib.error.URLError) as error:
            last_error = str(error)
        time.sleep(3)
    raise DeploymentError(f"API health check did not recover: {last_error}")


def verify_owner_login(
    api_url: str, email: str, password: str, timeout_seconds: int = 180
) -> None:
    payload = json.dumps({"email": email, "password": password}).encode()
    deadline = time.monotonic() + timeout_seconds
    last_error = "no response"
    while time.monotonic() < deadline:
        request = urllib.request.Request(
            f"{api_url.rstrip('/')}/api/v1/auth/login",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = json.loads(_open_without_proxy(request, 30))
            if (
                isinstance(response, dict)
                and response.get("email") == email
                and response.get("role") == "owner"
                and response.get("method") == "password"
            ):
                print(f"Initial Owner verified: {email} (role=owner, method=password)")
                return
            last_error = "unexpected identity response"
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            last_error = str(error)
        time.sleep(3)
    raise DeploymentError(f"Initial Owner password login failed: {last_error}")


def frontend_asset(index_path: Path = REPOSITORY_ROOT / "frontend" / "dist" / "index.html") -> str:
    match = re.search(
        r"assets/index-[A-Za-z0-9_-]+\.js", index_path.read_text(encoding="utf-8")
    )
    if match is None:
        raise DeploymentError("Frontend index does not identify its built JavaScript asset")
    return match.group(0)


def verify_frontend_asset(api_url: str, expected_asset: str) -> None:
    body = _open_without_proxy(urllib.request.Request(f"{api_url.rstrip('/')}/"), 30).decode(
        "utf-8"
    )
    if expected_asset not in body:
        raise DeploymentError(
            f"Served frontend asset does not match the package: expected {expected_asset}"
        )
    print(f"Frontend asset verified: {expected_asset}")


def verify_function_indexing(
    runner: CommandRunner,
    inputs: DeploymentInputs,
    outputs: Mapping[str, Any],
) -> None:
    resource_group = _output_string(outputs, "resourceGroupName")
    for output_name, expected in EXPECTED_FUNCTIONS.items():
        function_name = _output_string(outputs, output_name)
        result = runner.run(
            [
                "az",
                "functionapp",
                "function",
                "list",
                "--subscription",
                inputs.subscription,
                "--resource-group",
                resource_group,
                "--name",
                function_name,
                "--query",
                "[].name",
                "--output",
                "tsv",
            ],
            capture=True,
        )
        indexed = {name.rsplit("/", maxsplit=1)[-1] for name in result.stdout.splitlines()}
        missing = expected - indexed
        if missing:
            raise DeploymentError(
                f"Function app {function_name} did not index: {', '.join(sorted(missing))}"
            )


def verify_telemetry_function_health(outputs: Mapping[str, Any]) -> None:
    function_name = _output_string(outputs, "telemetryFunctionName")
    health_url = f"https://{function_name}.azurewebsites.net/api"
    wait_for_health(health_url, timeout_seconds=1800)
    print(f"Telemetry Function host verified: {function_name}")


def _confirm_deployment(assume_yes: bool) -> None:
    if assume_yes:
        return
    if input("Type 'deploy' to create or update these resources: ").strip() != "deploy":
        raise DeploymentError("Deployment cancelled")


def _outputs_path(inputs: DeploymentInputs) -> Path:
    return inputs.state_path.with_name(inputs.state_path.stem + ".outputs.json")


def load_saved_outputs(inputs: DeploymentInputs) -> dict[str, Any] | None:
    path = _outputs_path(inputs)
    if not path.exists():
        return None
    try:
        outputs = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DeploymentError(f"Invalid deployment outputs: {path}") from error
    if not isinstance(outputs, dict):
        raise DeploymentError(f"Deployment outputs must be an object: {path}")
    return outputs


def load_existing_core(inputs: DeploymentInputs) -> ExistingCore | None:
    outputs = load_saved_outputs(inputs)
    return ExistingCore.from_outputs(outputs) if outputs is not None else None


def _write_outputs(inputs: DeploymentInputs, outputs: Mapping[str, Any]) -> None:
    destination = _outputs_path(inputs)
    destination.write_text(json.dumps(outputs, indent=2) + "\n", encoding="utf-8")
    destination.chmod(0o600)
    print(f"Deployment outputs: {destination}")


def execute(args: argparse.Namespace, runner: CommandRunner) -> None:
    inputs = DeploymentInputs.load(
        args.subscription,
        args.parameters.resolve(),
        args.state.resolve() if args.state else None,
    )
    require_prerequisites(runner, inputs.subscription)
    if not args.allow_dirty:
        validate_source_snapshot(REPOSITORY_ROOT)
    saved_outputs = load_saved_outputs(inputs)
    if saved_outputs is None:
        validate_postgres_capabilities(runner, inputs)
    password_reader: PasswordReader = getpass.getpass
    if args.owner_credentials is not None:
        configured_password = owner_credentials_password(
            args.owner_credentials.resolve(), inputs.owner_email
        )

        def configured_password_reader(_: str) -> str:
            return configured_password

        password_reader = configured_password_reader
    secrets_ = load_or_create_secret_material(
        inputs,
        read_password=password_reader,
        require_owner_password=args.action == "deploy",
    )
    existing_core = (
        ExistingCore.from_outputs(saved_outputs) if saved_outputs is not None else None
    )
    base_parameters = deployment_parameters(
        inputs, secrets_, existing_core=existing_core
    )
    main_template = REPOSITORY_ROOT / "infra" / "main.bicep"
    release_template = REPOSITORY_ROOT / "infra" / "runtime-release.bicep"
    if saved_outputs is None:
        what_if(
            runner,
            inputs,
            main_template,
            base_parameters,
            f"{inputs.resource_prefix}-platform",
        )
        if args.action == "plan":
            return
        _confirm_deployment(args.yes)
        base_result = deploy_template(
            runner,
            inputs,
            main_template,
            base_parameters,
            f"{inputs.resource_prefix}-platform",
        )
        platform_outputs = deployment_outputs(base_result)
    else:
        platform_outputs = saved_outputs
        api_settings = current_app_settings(
            runner, inputs, _output_string(platform_outputs, "apiName")
        )
        control_plane_settings = current_app_settings(
            runner,
            inputs,
            _output_string(platform_outputs, "controlPlaneFunctionName"),
        )
        release_parameters = runtime_release_parameters(
            secrets_,
            platform_outputs,
            platform_outputs,
            api_settings,
            control_plane_settings,
        )
        what_if_resource_group(
            runner,
            inputs,
            release_template,
            release_parameters,
            f"{inputs.resource_prefix}-runtime-release",
        )
        if args.action == "plan":
            return
        _confirm_deployment(args.yes)
    version = source_version(runner)
    packages = build_packages(runner, inputs, version)
    expected_asset = frontend_asset()
    deploy_packages(runner, inputs, platform_outputs, packages)

    observer_document = observer_parameters(
        inputs,
        platform_outputs,
        secrets_,
        version,
        existing_observer=platform_outputs if saved_outputs is not None else None,
    )
    observer_template = REPOSITORY_ROOT / "infra" / "envoy-cache-adapter" / "main.bicep"
    what_if(
        runner,
        inputs,
        observer_template,
        observer_document,
        f"{inputs.resource_prefix}-observer",
    )
    observer_result = deploy_template(
        runner,
        inputs,
        observer_template,
        observer_document,
        f"{inputs.resource_prefix}-observer",
    )
    observer_outputs = deployment_outputs(observer_result)
    build_and_start_observer(runner, inputs, observer_outputs, version)
    wait_for_health(_output_string(observer_outputs, "webAppUrl"))

    api_settings = current_app_settings(
        runner, inputs, _output_string(platform_outputs, "apiName")
    )
    control_plane_settings = current_app_settings(
        runner,
        inputs,
        _output_string(platform_outputs, "controlPlaneFunctionName"),
    )
    release_parameters = runtime_release_parameters(
        secrets_,
        platform_outputs,
        observer_outputs,
        api_settings,
        control_plane_settings,
    )
    what_if_resource_group(
        runner,
        inputs,
        release_template,
        release_parameters,
        f"{inputs.resource_prefix}-runtime-release",
    )
    deploy_resource_group_template(
        runner,
        inputs,
        release_template,
        release_parameters,
        f"{inputs.resource_prefix}-runtime-release",
    )
    final_outputs = platform_outputs
    api_url = _output_string(final_outputs, "apiUrl")
    wait_for_health(api_url)
    verify_frontend_asset(api_url, expected_asset)
    if secrets_.owner_password is None:
        raise DeploymentError("Initial Owner password is required for verification")
    verify_owner_login(api_url, inputs.owner_email, secrets_.owner_password)
    verify_function_indexing(runner, inputs, final_outputs)
    verify_telemetry_function_health(final_outputs)
    _write_outputs(inputs, {**final_outputs, **observer_outputs})
    print("Turnstile deployment completed.")
    print(f"Open: {api_url}")
    print(
        "Foundry onboarding uses APIM principal "
        f"{_output_string(final_outputs, 'apimPrincipalId')} with Cognitive Services User."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan or deploy a complete self-hosted Turnstile environment."
    )
    parser.add_argument("action", choices=("plan", "deploy"))
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--state", type=Path)
    parser.add_argument(
        "--owner-credentials",
        type=Path,
        help="Optional private 0600 JSON file containing the Owner email and password.",
    )
    parser.add_argument("--yes", action="store_true")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Development only: package the current dirty worktree.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        execute(build_parser().parse_args(argv), CommandRunner())
    except (DeploymentError, subprocess.CalledProcessError) as error:
        print(f"Deployment failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())