from __future__ import annotations

import json
import re
import stat
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from scripts.deploy import (
    CommandRunner,
    DeploymentError,
    DeploymentInputs,
    ExistingCore,
    deployment_parameters,
    deterministic_zip,
    frontend_asset,
    linux_dependency_command,
    load_existing_core,
    load_or_create_secret_material,
    observer_names,
    observer_parameters,
    owner_credentials_password,
    pip_linux_dependency_command,
    runtime_release_parameters,
    temporary_parameter_file,
    what_if,
)
from scripts.stage_deployment import REPOSITORY_ROOT


def _parameters(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "parameters": {
                    "resourcePrefix": {"value": "turnstile"},
                    "resourceGroupName": {"value": "turnstile-test"},
                    "location": {"value": "eastus2"},
                    "apimPublisherEmail": {"value": "admin@example.com"},
                    "bootstrapOwnerEmail": {"value": "owner@example.com"},
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def test_secret_state_is_private_stable_and_excludes_plaintext(tmp_path: Path) -> None:
    inputs = DeploymentInputs.load(
        "00000000-0000-0000-0000-000000000001",
        _parameters(tmp_path / "parameters.json"),
        tmp_path / "state.json",
    )
    answers = iter(("a-secure-owner-password", "a-secure-owner-password"))
    first = load_or_create_secret_material(
        inputs,
        read_password=lambda _: next(answers),
        require_owner_password=True,
    )
    second = load_or_create_secret_material(
        inputs,
        read_password=lambda _: "a-secure-owner-password",
        require_owner_password=True,
    )

    assert first.values == second.values
    assert first.owner_password == "a-secure-owner-password"
    assert first.values["observerAdapterSharedKey"] != first.values["managementApiKey"]
    assert "a-secure-owner-password" not in inputs.state_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(inputs.state_path.stat().st_mode) == 0o600


def test_public_parameter_file_rejects_secure_values(tmp_path: Path) -> None:
    path = _parameters(tmp_path / "parameters.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    document["parameters"]["managementApiKey"] = {"value": "do-not-store-here"}
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(DeploymentError, match="Keep secure parameters out"):
        DeploymentInputs.load("subscription", path)


def test_owner_credentials_are_private_and_match_public_email(tmp_path: Path) -> None:
    path = tmp_path / "owner.credentials.json"
    path.write_text(
        json.dumps(
            {
                "email": "owner@example.com",
                "password": "a-secure-owner-password",
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)

    assert (
        owner_credentials_password(path, "owner@example.com")
        == "a-secure-owner-password"
    )
    with pytest.raises(DeploymentError, match="does not match"):
        owner_credentials_password(path, "other@example.com")


def test_owner_credentials_reject_group_or_world_access(tmp_path: Path) -> None:
    path = tmp_path / "owner.credentials.json"
    path.write_text(
        json.dumps(
            {
                "email": "owner@example.com",
                "password": "a-secure-owner-password",
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o644)

    with pytest.raises(DeploymentError, match="permissions must be 0600"):
        owner_credentials_password(path, "owner@example.com")


def test_base_parameters_disable_workers_until_observer_exists(tmp_path: Path) -> None:
    inputs = DeploymentInputs.load(
        "subscription",
        _parameters(tmp_path / "parameters.json"),
        tmp_path / "state.json",
    )
    answers = iter(("a-secure-owner-password", "a-secure-owner-password"))
    material = load_or_create_secret_material(
        inputs,
        read_password=lambda _: next(answers),
        require_owner_password=False,
    )

    parameters = deployment_parameters(inputs, material)["parameters"]

    assert parameters["provisionControlPlane"]["value"] is True
    assert parameters["controlPlaneEnabled"]["value"] is False
    assert parameters["gatewayReleaseWorkerEnabled"]["value"] is False
    assert parameters["apimUsageObserver"]["value"]["mode"] == "disabled"
    assert "observerAdapterSharedKey" not in parameters


def test_existing_core_skips_large_resources_and_gateway_bootstrap(tmp_path: Path) -> None:
    inputs = DeploymentInputs.load(
        "subscription",
        _parameters(tmp_path / "parameters.json"),
        tmp_path / "state.json",
    )
    answers = iter(("a-secure-owner-password", "a-secure-owner-password"))
    material = load_or_create_secret_material(
        inputs,
        read_password=lambda _: next(answers),
        require_owner_password=False,
    )
    core = ExistingCore(
        apim_name="apim-existing",
        apim_principal_id="00000000-0000-4000-8000-000000000010",
        apim_gateway_url="https://apim-existing.azure-api.net",
    )

    parameters = deployment_parameters(
        inputs, material, existing_core=core
    )["parameters"]

    assert parameters["provisionApimService"]["value"] is False
    assert parameters["provisionPostgres"]["value"] is False
    assert parameters["deployApimBootstrap"]["value"] is False
    assert parameters["existingApimName"]["value"] == core.apim_name
    assert parameters["existingApimPrincipalId"]["value"] == core.apim_principal_id
    assert parameters["existingApimGatewayUrl"]["value"] == core.apim_gateway_url


def test_saved_outputs_enable_existing_core_on_rerun(tmp_path: Path) -> None:
    inputs = DeploymentInputs.load(
        "subscription",
        _parameters(tmp_path / "parameters.json"),
        tmp_path / "state.json",
    )
    outputs_path = tmp_path / "state.outputs.json"
    outputs_path.write_text(
        json.dumps(
            {
                "apimName": "apim-existing",
                "apimPrincipalId": "00000000-0000-4000-8000-000000000010",
                "apimGatewayUrl": "https://apim-existing.azure-api.net",
            }
        ),
        encoding="utf-8",
    )

    core = load_existing_core(inputs)

    assert core is not None
    assert core.apim_name == "apim-existing"


def test_temporary_parameter_file_is_private_and_deleted(tmp_path: Path) -> None:
    with temporary_parameter_file({"parameters": {}}, tmp_path) as path:
        assert path.is_file()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not path.exists()


def test_observer_names_are_stable_and_azure_safe(tmp_path: Path) -> None:
    inputs = DeploymentInputs.load(
        "00000000-0000-0000-0000-000000000001",
        _parameters(tmp_path / "parameters.json"),
    )

    acr_name, web_app_name = observer_names(inputs)

    assert acr_name == observer_names(inputs)[0]
    assert acr_name.isalnum() and acr_name.islower() and len(acr_name) <= 50
    assert web_app_name.startswith("obs-turnstile-") and len(web_app_name) <= 60


def test_observer_parameters_reuse_saved_resource_names(tmp_path: Path) -> None:
    inputs = DeploymentInputs.load(
        "subscription",
        _parameters(tmp_path / "parameters.json"),
        tmp_path / "state.json",
    )
    answers = iter(("a-secure-owner-password", "a-secure-owner-password"))
    material = load_or_create_secret_material(
        inputs,
        read_password=lambda _: next(answers),
        require_owner_password=False,
    )
    document = observer_parameters(
        inputs,
        {
            "resourceGroupName": "turnstile-test",
            "appServicePlanName": "plan-turnstile-test",
            "eventHubNamespaceName": "eh-turnstile-test",
            "apimName": "apim-turnstile-test",
        },
        material,
        "abc123",
        existing_observer={
            "acrName": "acrexisting",
            "webAppName": "observer-existing",
            "observerAppServicePlanName": "plan-observer-existing",
        },
    )

    assert document["parameters"]["acrName"]["value"] == "acrexisting"
    assert document["parameters"]["webAppName"]["value"] == "observer-existing"
    assert (
        document["parameters"]["appServicePlanName"]["value"]
        == "plan-observer-existing"
    )
    assert document["parameters"]["provisionAcr"]["value"] is False


def test_deterministic_zip_has_stable_bytes_and_order(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "b.txt").write_text("b", encoding="utf-8")
    (source / "a.txt").write_text("a", encoding="utf-8")
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    deterministic_zip(source, first)
    deterministic_zip(source, second)

    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == ["a.txt", "b.txt"]


def test_linux_dependency_command_uses_pinned_target_platform(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "requirements.txt").write_text("fastapi==0.139.2\n", encoding="utf-8")

    command = linux_dependency_command(staged)

    assert command[:3] == ["uv", "pip", "install"]
    assert "x86_64-manylinux_2_17" in command
    assert "3.11" in command
    assert "--compile-bytecode" not in command
    assert "--no-compile" not in command


def test_pip_fallback_uses_pinned_target_platform(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "requirements.txt").write_text("fastapi==0.139.2\n", encoding="utf-8")

    command = pip_linux_dependency_command(staged, "/usr/bin/pip3")

    assert command[:2] == ["/usr/bin/pip3", "install"]
    assert "manylinux2014_x86_64" in command
    assert "3.11" in command
    assert "--only-binary=:all:" in command


def test_frontend_asset_reads_the_hashed_entrypoint(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    index.write_text(
        '<script type="module" src="/assets/index-Ab_12-c.js"></script>',
        encoding="utf-8",
    )

    assert frontend_asset(index) == "assets/index-Ab_12-c.js"


def test_repository_parameter_example_and_generated_documents_match_bicep(
    tmp_path: Path,
) -> None:
    inputs = DeploymentInputs.load(
        "00000000-0000-0000-0000-000000000001",
        REPOSITORY_ROOT / "infra" / "main.parameters.example.json",
        tmp_path / "state.json",
    )
    answers = iter(("a-secure-owner-password", "a-secure-owner-password"))
    material = load_or_create_secret_material(
        inputs,
        read_password=lambda _: next(answers),
        require_owner_password=False,
    )
    platform_outputs = {
        "resourceGroupName": inputs.resource_group_name,
        "appServicePlanName": "plan-turnstile-test",
        "eventHubNamespaceName": "eh-turnstile-test",
        "apimName": "apim-turnstile-test",
    }
    root_document = deployment_parameters(inputs, material)
    observer_document = observer_parameters(inputs, platform_outputs, material, "abc123")
    release_document = runtime_release_parameters(
        material,
        {
            "apiName": "api-turnstile-test",
            "controlPlaneFunctionName": "func-turnstile-control-test",
            "gatewayApiPath": "https://apim.test/turnstile/llm",
        },
        {
            "webAppUrl": "https://observer.test",
            "adapterKeyNamedValueName": "turnstile-observer-key",
        },
    )

    root_declared = set(
        re.findall(
            r"(?m)^param\s+(\w+)",
            (REPOSITORY_ROOT / "infra" / "main.bicep").read_text(encoding="utf-8"),
        )
    )
    observer_declared = set(
        re.findall(
            r"(?m)^param\s+(\w+)",
            (REPOSITORY_ROOT / "infra" / "envoy-cache-adapter" / "main.bicep").read_text(
                encoding="utf-8"
            ),
        )
    )
    release_declared = set(
        re.findall(
            r"(?m)^param\s+(\w+)",
            (REPOSITORY_ROOT / "infra" / "runtime-release.bicep").read_text(
                encoding="utf-8"
            ),
        )
    )

    assert set(root_document["parameters"]) <= root_declared
    assert set(observer_document["parameters"]) <= observer_declared
    assert set(release_document["parameters"]) <= release_declared


class WhatIfRunner(CommandRunner):
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result

    def run_json(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        del command, cwd, env
        return self.result


def test_what_if_reads_root_level_changes_and_rejects_delete(tmp_path: Path) -> None:
    inputs = DeploymentInputs.load(
        "subscription",
        _parameters(tmp_path / "parameters.json"),
        tmp_path / "state.json",
    )
    runner = WhatIfRunner(
        {
            "status": "Succeeded",
            "changes": [
                {"changeType": "Deploy", "resourceId": "/safe"},
                {"changeType": "Delete", "resourceId": "/unsafe"},
            ],
        }
    )

    with pytest.raises(DeploymentError, match="contains Delete changes"):
        what_if(
            runner,
            inputs,
            Path("infra/main.bicep"),
            {"parameters": {}},
            "test-deployment",
        )