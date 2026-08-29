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
OBSERVER_APP = (ROOT / "infra/envoy-cache-adapter/app.bicep").read_text(
    encoding="utf-8"
)
OBSERVER_MAIN = (ROOT / "infra/envoy-cache-adapter/main.bicep").read_text(
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


def test_new_apim_defaults_to_standard_v2() -> None:
    assert "param apimSkuName string = 'StandardV2'" in MAIN
    assert PARAMETERS["apimSkuName"]["value"] == "StandardV2"


def test_apim_exposes_the_turnstile_gateway_path() -> None:
    assert "param apimApiId string = 'turnstile-llm'" in MAIN
    assert "param gatewayApiRelativePath string = 'turnstile/llm'" in MAIN
    assert "name: apiId" in APIM_INTEGRATION
    assert "displayName: '${apiId} | Turnstile AI Gateway'" in APIM_INTEGRATION
    assert "param apiPath string" in APIM_INTEGRATION
    assert "path: apiPath" in APIM_INTEGRATION
    assert "path: 'finops/llm'" not in APIM_INTEGRATION


def test_shared_apim_resources_are_environment_isolated() -> None:
    for parameter, resource_name in (
        ("apiId", "apiId"),
        ("productId", "productId"),
        ("dashboardSubscriptionId", "dashboardSubscriptionId"),
        ("probeSubscriptionId", "probeSubscriptionId"),
        ("appInsightsLoggerId", "appInsightsLoggerId"),
        ("eventHubLoggerId", "eventHubLoggerId"),
        ("diagnosticSettingName", "diagnosticSettingName"),
    ):
        assert f"param {parameter} string" in APIM_INTEGRATION
        assert f"name: {resource_name}" in APIM_INTEGRATION

    assert "displayName: '${apiId} | Turnstile AI Gateway'" in APIM_INTEGRATION
    assert "displayName: '${productId} | Turnstile AI'" in APIM_INTEGRATION
    assert "param existingApimResourceGroupName string = ''" in MAIN
    assert "scope: effectiveApimResourceGroup" in MAIN


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


def test_employee_token_policy_is_disabled_without_entra_configuration() -> None:
    assert PARAMETERS["entraClientId"]["value"] == ""
    assert (
        "var employeeTokenEnabled = !empty(trim(employeeClientId)) "
        "&& !empty(trim(employeeAudience))"
    ) in APIM_INTEGRATION
    assert "employeeTokenEnabled ? 'true' : 'false'" in APIM_INTEGRATION
    assert "employeeTokenEnabled ? trim(employeeClientId)" in APIM_INTEGRATION
    assert "employeeTokenEnabled ? trim(employeeAudience)" in APIM_INTEGRATION
    assert "@(__EMPLOYEE_TOKEN_ENABLED__ &amp;&amp;" in (
        ROOT / "infra/policies/foundry-finops-policy.xml"
    ).read_text(encoding="utf-8")


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


def test_functions_use_separate_flex_consumption_plans() -> None:
    telemetry = DATA_PLANE.split("resource functionApp", 1)[1].split(
        "resource functionStorageBlobOwner", 1
    )[0]
    control = CONTROL_PLANE.split("resource functionApp", 1)[1].split(
        "resource keyVault", 1
    )[0]

    assert "serverFarmId: apiPlan.id" in DATA_PLANE
    assert "serverFarmId: telemetryPlan.id" in telemetry
    assert "serverFarmId: plan.id" in control
    assert DATA_PLANE.count("tier: 'FlexConsumption'") == 1
    assert CONTROL_PLANE.count("tier: 'FlexConsumption'") == 1
    assert "name: 'FC1'" in DATA_PLANE
    assert "name: 'FC1'" in CONTROL_PLANE
    for template in (telemetry, control):
        assert "functionAppConfig:" in template
        assert "instanceMemoryMB: 2048" in template
        assert "maximumInstanceCount: 100" in template
        assert "name: 'python'" in template
        assert "version: '3.11'" in template
        assert "type: 'SystemAssignedIdentity'" in template
        for incompatible in (
            "alwaysOn:",
            "linuxFxVersion:",
            "vnetRouteAllEnabled:",
            "WEBSITE_RUN_FROM_PACKAGE",
            "WEBSITES_CONTAINER_START_TIME_LIMIT",
            "SCM_DO_BUILD_DURING_DEPLOYMENT",
            "FUNCTIONS_WORKER_RUNTIME",
        ):
            assert incompatible not in template


def test_observer_uses_a_configurable_premium_v3_plan() -> None:
    assert "param appServicePlanSkuName string = 'P0v3'" in OBSERVER_APP
    assert "param appServicePlanWorkerCount int = 1" in OBSERVER_APP
    assert "name: appServicePlanSkuName" in OBSERVER_APP
    assert "tier: 'PremiumV3'" in OBSERVER_APP
    assert "capacity: appServicePlanWorkerCount" in OBSERVER_APP
    assert "appServicePlanSkuName: appServicePlanSkuName" in OBSERVER_MAIN
    assert "appServicePlanWorkerCount: appServicePlanWorkerCount" in OBSERVER_MAIN


def test_flex_functions_have_isolated_network_and_deployment_storage() -> None:
    assert "addressPrefix: '10.42.3.0/27'" in DATA_PLANE
    assert "addressPrefix: '10.42.3.32/27'" in DATA_PLANE
    assert DATA_PLANE.count("serviceName: 'Microsoft.App/environments'") == 2
    assert "var telemetryDeploymentContainerName = 'deploy-telemetry'" in DATA_PLANE
    assert "name: telemetryDeploymentContainerName" in DATA_PLANE
    assert "var deploymentContainerName = 'deploy-control-plane'" in CONTROL_PLANE
    assert "name: deploymentContainerName" in CONTROL_PLANE
    assert "resource functionVnetIntegration 'Microsoft.Web/sites/networkConfig@" in (
        DATA_PLANE
    )
    assert "resource functionVnetIntegration 'Microsoft.Web/sites/networkConfig@" in (
        CONTROL_PLANE
    )


def test_vnet_subnets_are_created_serially() -> None:
    telemetry_subnet = DATA_PLANE.split("resource telemetryFunctionSubnet", 1)[1].split(
        "resource controlFunctionSubnet", 1
    )[0]
    control_subnet = DATA_PLANE.split("resource controlFunctionSubnet", 1)[1].split(
        "resource privateEndpointSubnet", 1
    )[0]
    private_endpoint_subnet = DATA_PLANE.split("resource privateEndpointSubnet", 1)[
        1
    ].split("resource blobPrivateDnsZone", 1)[0]
    assert "virtualNetwork" in telemetry_subnet.split("dependsOn:", 1)[1]
    assert "telemetryFunctionSubnet" in control_subnet.split("dependsOn:", 1)[1]
    assert "controlFunctionSubnet" in private_endpoint_subnet.split("dependsOn:", 1)[1]


def test_telemetry_host_storage_is_fully_reachable_over_private_links() -> None:
    for service in ("blob", "queue", "table"):
        assert f"privatelink.{service}.${{environment().suffixes.storage}}" in DATA_PLANE
        assert f"name: 'pe-${{storageName}}-{service}'" in DATA_PLANE
        assert f"'{service}'" in DATA_PLANE
    for setting in (
        "AzureWebJobsStorage__blobServiceUri",
        "AzureWebJobsStorage__queueServiceUri",
        "AzureWebJobsStorage__tableServiceUri",
    ):
        assert setting in DATA_PLANE
    assert "resource functionStorageQueueContributor" in DATA_PLANE
    assert "resource functionStorageTableContributor" in DATA_PLANE


def test_api_and_apim_share_the_same_gateway_path() -> None:
    assert "param gatewayApiRelativePath string = 'turnstile/llm'" in MAIN
    assert (
        "var effectiveGatewayApiPath = "
        "'${effectiveApimGatewayUrl}/${gatewayApiRelativePath}'"
        in MAIN
    )
    assert "apimGatewayUrl: effectiveGatewayApiPath" in MAIN
    assert "apimApiId: apimApiId" in MAIN
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


def test_control_plane_uses_its_own_flex_plan_and_the_platform_vnet() -> None:
    assert "appServicePlanName:" not in MAIN.split("module controlPlane", 1)[1].split(
        "module controlPlaneApimRbac", 1
    )[0]
    assert "resourcePrefix: resourcePrefix" in MAIN
    assert "virtualNetworkName: 'vnet-${resourcePrefix}-${suffix}'" in MAIN
    assert "apimGatewayUrl: effectiveGatewayApiPath" in MAIN
    assert "var storageName = 'stturnstilecp${take(suffix, 11)}'" in CONTROL_PLANE
    assert "var functionName = 'func-${resourcePrefix}-control-${suffix}'" in CONTROL_PLANE
    assert "var planName = 'plan-${resourcePrefix}-control-${suffix}'" in CONTROL_PLANE
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


def test_custom_roles_use_installation_specific_names() -> None:
    for template in (CONTROL_PLANE_APIM_RBAC, APPLICATION_KEY_MANAGEMENT_RBAC):
        assert "var roleNameSuffix = uniqueString(resourceGroup().id)" in template
        assert "${roleNameSuffix}'" in template
    assert "roleName: 'Turnstile APIM Publisher ${roleNameSuffix}'" in CONTROL_PLANE_APIM_RBAC
    assert (
        "roleName: 'Turnstile APIM Subscription Key Operator ${roleNameSuffix}'"
        in APPLICATION_KEY_MANAGEMENT_RBAC
    )
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
