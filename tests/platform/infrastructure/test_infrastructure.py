from __future__ import annotations

import json

from tests.support.paths import REPOSITORY_ROOT

ROOT = REPOSITORY_ROOT
MAIN = (ROOT / "infra/main.bicep").read_text(encoding="utf-8")
DATA_PLANE = (ROOT / "infra/modules/data-plane.bicep").read_text(encoding="utf-8")
APIM_SERVICE = (ROOT / "infra/modules/apim-service.bicep").read_text(encoding="utf-8")
APIM_INTEGRATION = (ROOT / "infra/modules/apim-integration.bicep").read_text(
    encoding="utf-8"
)
CONTROL_PLANE = (ROOT / "infra/modules/control-plane-function.bicep").read_text(
    encoding="utf-8"
)
CONTROL_PLANE_APIM_RBAC = (
    ROOT / "infra/modules/control-plane-apim-rbac.bicep"
).read_text(encoding="utf-8")
APPLICATION_KEY_MANAGEMENT_RBAC = (
    ROOT / "infra/modules/application-key-management-rbac.bicep"
).read_text(encoding="utf-8")
INITIAL_SCHEMA = (ROOT / "migrations/001_initial_schema.up.sql").read_text(
    encoding="utf-8"
)
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


def test_apim_exposes_the_turnstile_gateway_path() -> None:
    assert "name: 'turnstile-llm'" in APIM_INTEGRATION
    assert "displayName: 'Turnstile AI Gateway'" in APIM_INTEGRATION
    assert "var gatewayApiRelativePath = 'turnstile/llm'" in MAIN
    assert "param apiPath string" in APIM_INTEGRATION
    assert "path: apiPath" in APIM_INTEGRATION
    assert "path: 'finops/llm'" not in APIM_INTEGRATION


def test_fresh_apim_creates_its_azure_monitor_logger() -> None:
    assert (
        "resource azureMonitorLogger 'Microsoft.ApiManagement/service/loggers@"
        in APIM_INTEGRATION
    )
    assert "name: 'azuremonitor'" in APIM_INTEGRATION
    assert "loggerType: 'azureMonitor'" in APIM_INTEGRATION
    assert "loggerId: azureMonitorLogger.id" in APIM_INTEGRATION
    logger = APIM_INTEGRATION.split("resource azureMonitorLogger", 1)[1].split(
        "resource llmDiagnostics", 1
    )[0]
    assert "credentials:" not in logger


def test_employee_token_policy_defaults_to_the_login_tenant_and_client() -> None:
    assert "param employeeTenantId string = subscription().tenantId" in MAIN
    assert "param employeeClientId string = entraClientId" in MAIN
    assert "param employeeAudience string = entraClientId" in MAIN


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


def test_api_python_path_contains_package_root_and_prebuilt_dependencies() -> None:
    assert (
        "{ name: 'PYTHONPATH', value: "
        "'/home/site/wwwroot:/home/site/wwwroot/.python_packages/lib/site-packages' }"
        in DATA_PLANE
    )


def test_functions_allow_vnet_cold_start_to_complete() -> None:
    startup_limit = (
        "{ name: 'WEBSITES_CONTAINER_START_TIME_LIMIT', value: '1800' }"
    )
    assert DATA_PLANE.count(startup_limit) == 2
    assert startup_limit in CONTROL_PLANE


def test_api_and_apim_share_the_same_gateway_path() -> None:
    assert "var gatewayApiRelativePath = 'turnstile/llm'" in MAIN
    assert (
        "var effectiveGatewayApiPath = "
        "'${effectiveApimGatewayUrl}/${gatewayApiRelativePath}'"
        in MAIN
    )
    assert "apimGatewayUrl: effectiveGatewayApiPath" in MAIN
    assert "apiPath: gatewayApiRelativePath" in MAIN
    assert "{ name: 'APIM_GATEWAY_URL', value: apimGatewayUrl }" in DATA_PLANE
    assert (
        "{ name: 'APIM_DASHBOARD_SUBSCRIPTION_KEY', value: apimSubscriptionKey }"
        in DATA_PLANE
    )
    assert "path: apiPath" in APIM_INTEGRATION


def test_api_bootstraps_initial_owner_between_migration_and_startup() -> None:
    assert (
        "appCommandLine: 'python -m backend.migrate && python -m backend.bootstrap "
        "&& python -m uvicorn backend.api:app"
        in DATA_PLANE
    )
    assert "{ name: 'BOOTSTRAP_OWNER_EMAIL', value: bootstrapOwnerEmail }" in DATA_PLANE
    assert (
        "{ name: 'BOOTSTRAP_OWNER_PASSWORD_HASH', value: bootstrapOwnerPasswordHash }"
        in DATA_PLANE
    )


def test_collapsed_migration_restores_search_path_after_pg_dump() -> None:
    dump_end, historical_migrations = INITIAL_SCHEMA.split(
        "-- Integrated from historical migration 041_pinned_report_layout.up.sql.", 1
    )
    assert "SELECT pg_catalog.set_config('search_path', '', false);" in dump_end
    assert dump_end.rstrip().endswith("SET search_path = public, pg_catalog;")
    assert "CREATE TABLE pinned_report_layout" in historical_migrations


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


def test_control_plane_uses_the_data_plane_plan_and_vnet() -> None:
    assert "appServicePlanName: 'plan-${resourcePrefix}-${suffix}'" in MAIN
    assert "virtualNetworkName: 'vnet-${resourcePrefix}-${suffix}'" in MAIN
    assert "apimGatewayUrl: effectiveGatewayApiPath" in MAIN
    assert "var storageName = 'stturnstilecp${take(suffix, 11)}'" in CONTROL_PLANE
    assert "var functionName = 'func-turnstile-control-${suffix}'" in CONTROL_PLANE
    assert "plan-finops" not in CONTROL_PLANE
    assert "vnet-finops" not in CONTROL_PLANE


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


def test_custom_roles_use_product_specific_names() -> None:
    assert "roleName: 'Turnstile APIM Publisher'" in CONTROL_PLANE_APIM_RBAC
    assert "roleName: 'Turnstile APIM Subscription Key Operator'" in APPLICATION_KEY_MANAGEMENT_RBAC
    assert "roleName: 'FinOps" not in CONTROL_PLANE_APIM_RBAC
    assert "roleName: 'FinOps" not in APPLICATION_KEY_MANAGEMENT_RBAC


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
