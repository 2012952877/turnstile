targetScope = 'subscription'

type DisabledUsageObserver = {
  mode: 'disabled'
  url: ''
  keyNamedValue: ''
  legacyFoundryUpstreamHost: ''
  legacyFoundryUpstreamBasePath: ''
  legacyDatabricksUpstreamHost: ''
  legacyDatabricksUpstreamBasePath: ''
}

type EnabledUsageObserver = {
  mode: 'enabled'
  @minLength(1)
  url: string
  @minLength(1)
  keyNamedValue: string
  @minLength(1)
  legacyFoundryUpstreamHost: string
  @minLength(1)
  legacyFoundryUpstreamBasePath: string
  @minLength(1)
  legacyDatabricksUpstreamHost: string
  @minLength(1)
  legacyDatabricksUpstreamBasePath: string
}

@description('Short lowercase prefix used in globally unique resource names.')
@minLength(3)
@maxLength(12)
param resourcePrefix string = 'turnstile'

@description('Resource group created for the Turnstile platform.')
param resourceGroupName string = '${resourcePrefix}-platform'

param location string
param postgresLocation string = location

@description('APIM publisher contact shown by Azure.')
param apimPublisherEmail string

@description('APIM publisher display name.')
param apimPublisherName string = 'Turnstile'

@allowed([
  'Developer'
  'BasicV2'
  'StandardV2'
])
param apimSkuName string = 'Developer'

@minValue(1)
param apimCapacity int = 1

@secure()
@minLength(16)
param postgresAdministratorPassword string

@secure()
param credentialEncryptionKey string

@secure()
param managementApiKey string

@secure()
param apimSubscriptionKey string

@secure()
param apimProbeSubscriptionKey string

@minValue(1)
param apimTokensPerMinute int = 100000

@description('Optional onboarded model alias used by publication probes. Leave empty on first deployment.')
param apimRegressionModelKey string = ''

@description('Temporary upgrade bridge for an existing gateway whose legacy Foundry/Databricks models have not yet been adopted into dynamic publication bindings. Fresh deployments must leave this false.')
param preserveLegacyProviderRouting bool = false

@description('Entra tenant that issues employee tokens for interactive clients such as Claude Desktop.')
param employeeTenantId string = ''

@description('Public client application ID used by employee desktop clients.')
param employeeClientId string = ''

@description('Application ID of the model API that employee tokens are issued for.')
param employeeAudience string = ''

@description('Per-minute burst ceiling for each validated employee. The monthly allowance is enforced from the Table Storage ledger, not from an APIM counter.')
@minValue(1)
param employeeTokensPerMinute int = 100000

@description('Budget ledger table name.')
param ledgerTableName string = 'TurnstileLedger'

@description('Organization ID attributed to employee desktop traffic. Must equal the enterprise catalog organization id, or employee usage rolls up to an organization that does not exist and the parent total comes out smaller than its own department.')
param employeeOrgId string = 'organization-default'

@description('Organization name attributed to employee desktop traffic. Must match the catalog organization name for the same reason.')
param employeeOrgName string = 'Default Organization'

@description('Maps FinOps department IDs, emitted as Entra app roles, to their display names.')
param employeeDepartmentMap object = {}

@description('Maps Entra client application IDs to the Agent name attributed to their traffic.')
param employeeClientMap object = {}

@description('Refines the Agent name from the User-Agent, most specific entry first. Every token must be present for an entry to match; no match keeps the client-map name.')
param employeeSurfaceMap array = []

@description('Maps trusted APIM subscription IDs to Agent identities for subscription-key callers.')
param subscriptionAgentMap object = {}

@description('Public client ID of the Microsoft Entra SPA used by the Turnstile web app.')
param entraClientId string = ''

@description('Exact email domains allowed to sign in through Microsoft Entra.')
param entraAllowedEmailDomains array = []

@description('Provision the isolated APIM publication Function with publishing disabled until explicitly enabled.')
param provisionControlPlane bool = false

@description('Allow the isolated control-plane Function to process queued gateway publications.')
param controlPlaneEnabled bool = false

@description('Allow the isolated control-plane Function to process queued Gateway Release operations.')
param gatewayReleaseWorkerEnabled bool = false

@description('Allow Owners to reveal and rotate governed APIM subscription keys through the Turnstile API.')
param gatewayApplicationKeyManagementEnabled bool = false

@description('All-or-nothing transparent usage observer configuration.')
@discriminator('mode')
param apimUsageObserver DisabledUsageObserver | EnabledUsageObserver = {
  mode: 'disabled'
  url: ''
  keyNamedValue: ''
  legacyFoundryUpstreamHost: ''
  legacyFoundryUpstreamBasePath: ''
  legacyDatabricksUpstreamHost: ''
  legacyDatabricksUpstreamBasePath: ''
}

@description('Activate historical fallback observer routes only after route reconciliation is active.')
param apimUsageObserverLegacyRoutingEnabled bool = false

param postgresAdministratorLogin string = 'turnstileadmin'
param postgresSkuName string = 'Standard_B1ms'
param postgresTier string = 'Burstable'
param suffix string = uniqueString(subscription().id, resourceGroupName)

resource platformResourceGroup 'Microsoft.Resources/resourceGroups@2024-11-01' = {
  name: resourceGroupName
  location: location
  tags: {
    workload: 'turnstile'
    dataClassification: 'usage-metadata-only'
  }
}

module apim 'modules/apim-service.bicep' = {
  name: 'turnstile-apim'
  scope: platformResourceGroup
  params: {
    name: 'apim-${resourcePrefix}-${suffix}'
    location: location
    publisherEmail: apimPublisherEmail
    publisherName: apimPublisherName
    skuName: apimSkuName
    capacity: apimCapacity
  }
}

module dataPlane 'modules/data-plane.bicep' = {
  name: 'turnstile-data-plane'
  scope: platformResourceGroup
  params: {
    location: location
    postgresLocation: postgresLocation
    resourcePrefix: resourcePrefix
    suffix: suffix
    postgresAdministratorLogin: postgresAdministratorLogin
    postgresAdministratorPassword: postgresAdministratorPassword
    postgresSkuName: postgresSkuName
    postgresTier: postgresTier
    credentialEncryptionKey: credentialEncryptionKey
    managementApiKey: managementApiKey
    apimSubscriptionKey: apimSubscriptionKey
    apimProbeSubscriptionKey: apimProbeSubscriptionKey
    apimPrincipalId: apim.outputs.principalId
    apimResourceGroupName: platformResourceGroup.name
    apimName: apim.outputs.name
    ledgerTableName: ledgerTableName
    gatewayReleaseWorkerEnabled: provisionControlPlane && gatewayReleaseWorkerEnabled && apimUsageObserver.mode == 'enabled'
    gatewayApplicationKeyManagementEnabled: gatewayApplicationKeyManagementEnabled
    entraClientId: entraClientId
    entraAllowedEmailDomains: entraAllowedEmailDomains
  }
}

module apimIntegration 'modules/apim-integration.bicep' = {
  name: 'turnstile-apim-integration'
  scope: platformResourceGroup
  params: {
    apimName: apim.outputs.name
    eventHubNamespaceResourceId: dataPlane.outputs.eventHubNamespaceResourceId
    eventHubNamespaceName: dataPlane.outputs.eventHubNamespaceName
    eventHubName: dataPlane.outputs.eventHubName
    apimSubscriptionKey: apimSubscriptionKey
    apimProbeSubscriptionKey: apimProbeSubscriptionKey
    tokensPerMinute: apimTokensPerMinute
    preserveLegacyProviderRouting: preserveLegacyProviderRouting
    usageObserver: apimUsageObserver
    legacyObserverRoutingEnabled: apimUsageObserverLegacyRoutingEnabled
    employeeTenantId: employeeTenantId
    employeeClientId: employeeClientId
    employeeAudience: employeeAudience
    employeeTokensPerMinute: employeeTokensPerMinute
    ledgerTableEndpoint: dataPlane.outputs.ledgerTableEndpoint
    ledgerTableName: ledgerTableName
    employeeOrgId: employeeOrgId
    employeeOrgName: employeeOrgName
    employeeDepartmentMap: employeeDepartmentMap
    employeeClientMap: employeeClientMap
    employeeSurfaceMap: employeeSurfaceMap
    subscriptionAgentMap: subscriptionAgentMap
    appInsightsName: dataPlane.outputs.applicationInsightsName
    appInsightsResourceGroupName: platformResourceGroup.name
  }
}

module controlPlane 'modules/control-plane-function.bicep' = if (provisionControlPlane) {
  name: 'turnstile-control-plane-function'
  scope: platformResourceGroup
  params: {
    location: location
    suffix: suffix
    keyVaultName: dataPlane.outputs.keyVaultName
    databaseUrlSecretUri: dataPlane.outputs.databaseUrlSecretUri
    apimProbeSubscriptionKeySecretUri: dataPlane.outputs.apimProbeSubscriptionKeySecretUri
    credentialEncryptionKeySecretUri: dataPlane.outputs.credentialEncryptionKeySecretUri
    applicationInsightsConnectionString: dataPlane.outputs.applicationInsightsConnectionString
    apimResourceGroupName: platformResourceGroup.name
    apimName: apim.outputs.name
    apimGatewayUrl: apimIntegration.outputs.gatewayUrl
    regressionModelKey: apimRegressionModelKey
    usageObserverUrl: apimUsageObserver.url
    usageObserverKeyNamedValue: apimUsageObserver.keyNamedValue
    subscriptionAgentMap: subscriptionAgentMap
    publicationWorkerEnabled: controlPlaneEnabled
    releaseWorkerEnabled: gatewayReleaseWorkerEnabled
  }
}

module controlPlaneApimRbac 'modules/control-plane-apim-rbac.bicep' = if (provisionControlPlane) {
  name: 'turnstile-control-plane-apim-rbac'
  scope: platformResourceGroup
  params: {
    apimName: apim.outputs.name
    controlPlanePrincipalId: controlPlane!.outputs.principalId
  }
}

module applicationKeyManagementRbac 'modules/application-key-management-rbac.bicep' = if (gatewayApplicationKeyManagementEnabled) {
  name: 'turnstile-application-key-management-rbac'
  scope: platformResourceGroup
  params: {
    apimName: apim.outputs.name
    apiPrincipalIds: [
      dataPlane.outputs.apiPrincipalId
    ]
  }
}

output resourceGroupName string = platformResourceGroup.name
output apimName string = apim.outputs.name
output postgresServerName string = dataPlane.outputs.postgresServerName
output eventHubNamespaceName string = dataPlane.outputs.eventHubNamespaceName
output applicationInsightsName string = dataPlane.outputs.applicationInsightsName
output apimGatewayUrl string = apimIntegration.outputs.gatewayUrl
output gatewayApiPath string = apimIntegration.outputs.gatewayApiPath
output controlPlaneFunctionName string = provisionControlPlane ? controlPlane!.outputs.functionName : ''
