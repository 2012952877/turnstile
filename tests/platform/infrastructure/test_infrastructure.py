from __future__ import annotations

import json

from tests.support.paths import REPOSITORY_ROOT

ROOT = REPOSITORY_ROOT
MAIN = (ROOT / "infra/main.bicep").read_text(encoding="utf-8")
DATA_PLANE = (ROOT / "infra/modules/data-plane.bicep").read_text(encoding="utf-8")
APIM_SERVICE = (ROOT / "infra/modules/apim-service.bicep").read_text(encoding="utf-8")
CONTROL_PLANE = (ROOT / "infra/modules/control-plane-function.bicep").read_text(
    encoding="utf-8"
)
CONTROL_PLANE_APIM_RBAC = (
    ROOT / "infra/modules/control-plane-apim-rbac.bicep"
).read_text(encoding="utf-8")
APPLICATION_KEY_MANAGEMENT_RBAC = (
    ROOT / "infra/modules/application-key-management-rbac.bicep"
).read_text(encoding="utf-8")
PARAMETERS = json.loads(
    (ROOT / "infra/main.parameters.example.json").read_text(encoding="utf-8")
)["parameters"]


def test_main_creates_the_platform_resource_group_and_apim() -> None:
    assert "targetScope = 'subscription'" in MAIN
    assert "resource platformResourceGroup" in MAIN
    assert "module apim 'modules/apim-service.bicep'" in MAIN
    assert "scope: platformResourceGroup" in MAIN
    assert "type: 'SystemAssigned'" in APIM_SERVICE
    assert "publisherEmail: publisherEmail" in APIM_SERVICE


def test_templates_create_platform_resources_without_foundry_projects_or_models() -> None:
    for resource in (
        "Microsoft.DBforPostgreSQL/flexibleServers@",
        "Microsoft.EventHub/namespaces@",
        "Microsoft.Storage/storageAccounts@",
        "Microsoft.KeyVault/vaults@",
        "Microsoft.Insights/components@",
        "Microsoft.Web/sites@",
    ):
        assert resource in DATA_PLANE
    combined = MAIN + DATA_PLANE + APIM_SERVICE + CONTROL_PLANE
    assert "Microsoft.CognitiveServices/accounts/projects" not in combined
    assert "Microsoft.CognitiveServices/accounts/deployments" not in combined


def test_data_plane_creates_an_entra_only_budget_ledger() -> None:
    assert "resource ledgerStorage 'Microsoft.Storage/storageAccounts@" in DATA_PLANE
    assert (
        "resource ledgerTable 'Microsoft.Storage/storageAccounts/tableServices/tables@"
        in DATA_PLANE
    )
    assert "allowSharedKeyAccess: false" in DATA_PLANE
    assert "publicNetworkAccess: 'Enabled'" in DATA_PLANE
    assert "bypass: 'AzureServices'" in DATA_PLANE
    assert "name: ledgerTableName" in DATA_PLANE
    assert "output ledgerTableEndpoint string = ledgerTableEndpoint" in DATA_PLANE


def test_ledger_and_telemetry_roles_are_deterministic_and_scoped() -> None:
    assert "resource telemetryLedgerContributor" in DATA_PLANE
    assert "resource apimLedgerContributor" in DATA_PLANE
    assert "scope: ledgerTable" in DATA_PLANE
    assert "0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3" in DATA_PLANE
    assert "name: guid(workspace.id, functionApp.id, 'log-analytics-reader')" in DATA_PLANE


def test_control_plane_features_default_to_disabled() -> None:
    for declaration in (
        "param provisionControlPlane bool = false",
        "param controlPlaneEnabled bool = false",
        "param gatewayReleaseWorkerEnabled bool = false",
        "param gatewayApplicationKeyManagementEnabled bool = false",
    ):
        assert declaration in MAIN
    assert "effectivePublicationWorkerEnabled = publicationWorkerEnabled" in CONTROL_PLANE
    assert "effectiveReleaseWorkerEnabled = releaseWorkerEnabled" in CONTROL_PLANE


def test_application_key_management_role_is_least_privilege() -> None:
    actions = {
        line.strip().strip("'")
        for line in APPLICATION_KEY_MANAGEMENT_RBAC.split("actions: [", 1)[1]
        .split("]", 1)[0]
        .splitlines()
        if line.strip().startswith("'")
    }
    assert actions == {
        "Microsoft.ApiManagement/service/subscriptions/read",
        "Microsoft.ApiManagement/service/subscriptions/listSecrets/action",
        "Microsoft.ApiManagement/service/subscriptions/regeneratePrimaryKey/action",
        "Microsoft.ApiManagement/service/subscriptions/regenerateSecondaryKey/action",
    }
    assert "scope: apim" in APPLICATION_KEY_MANAGEMENT_RBAC


def test_control_plane_role_has_no_subscription_or_resource_group_scope() -> None:
    assert "scope: apim" in CONTROL_PLANE_APIM_RBAC
    assert "assignableScopes: [" in CONTROL_PLANE_APIM_RBAC
    assert "resourceGroup().id" in CONTROL_PLANE_APIM_RBAC


def test_example_parameters_are_non_secret_and_environment_neutral() -> None:
    assert PARAMETERS["resourcePrefix"]["value"] == "turnstile"
    assert PARAMETERS["entraClientId"]["value"] == ""
    assert PARAMETERS["entraAllowedEmailDomains"]["value"] == []
    for secret in (
        "postgresAdministratorPassword",
        "credentialEncryptionKey",
        "managementApiKey",
        "apimSubscriptionKey",
        "apimProbeSubscriptionKey",
    ):
        assert secret not in PARAMETERS
    serialized = json.dumps(PARAMETERS)
    for forbidden in ("xle-", "sebpvmm", "2b04b108", "azurewebsites.net"):
        assert forbidden not in serialized
