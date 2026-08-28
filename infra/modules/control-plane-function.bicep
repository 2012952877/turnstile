targetScope = 'resourceGroup'

param location string
param suffix string
param appServicePlanName string = 'plan-turnstile-${suffix}'
param virtualNetworkName string = 'vnet-turnstile-${suffix}'
param functionSubnetName string = 'snet-functions'
param keyVaultName string
param databaseUrlSecretUri string
param apimProbeSubscriptionKeySecretUri string
param credentialEncryptionKeySecretUri string
param applicationInsightsConnectionString string
param apimResourceGroupName string
param apimName string
param apimApiId string = 'turnstile-llm'
param chatCompletionsOperationId string = 'chat-completions'
param responsesOperationId string = 'responses'
param responsesCompactOperationId string = 'responses-compact'
param messagesOperationId string = 'anthropic-messages'
param countTokensOperationId string = 'anthropic-count-tokens'
param modelsOperationId string = 'anthropic-models'
param apimGatewayUrl string
param regressionModelKey string
param usageObserverUrl string = ''
param usageObserverKeyNamedValue string = ''
param subscriptionAgentMap object = {}
param publicationWorkerEnabled bool = false
param releaseWorkerEnabled bool = false

var storageName = 'stturnstilecp${take(suffix, 11)}'
var functionName = 'func-turnstile-control-${suffix}'
var observerConfigured = !empty(trim(usageObserverUrl)) && !empty(trim(usageObserverKeyNamedValue))
var effectivePublicationWorkerEnabled = publicationWorkerEnabled && observerConfigured
var effectiveReleaseWorkerEnabled = releaseWorkerEnabled && observerConfigured
var effectiveEnabled = effectivePublicationWorkerEnabled || effectiveReleaseWorkerEnabled

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: {
    SecurityControl: 'Ignore'
  }
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowSharedKeyAccess: false
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

resource plan 'Microsoft.Web/serverfarms@2024-11-01' existing = {
  name: appServicePlanName
}

resource virtualNetwork 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: virtualNetworkName
}

resource functionSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: virtualNetwork
  name: functionSubnetName
}

resource functionApp 'Microsoft.Web/sites@2024-11-01' = {
  name: functionName
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    clientAffinityEnabled: false
    virtualNetworkSubnetId: functionSubnet.id
    outboundVnetRouting: {
      allTraffic: true
    }
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      alwaysOn: true
      vnetRouteAllEnabled: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: [
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'AzureWebJobsFeatureFlags', value: 'EnableWorkerIndexing' }
        { name: 'AzureWebJobsStorage__accountName', value: storage.name }
        { name: 'AzureWebJobsStorage__blobServiceUri', value: storage.properties.primaryEndpoints.blob }
        { name: 'AzureWebJobsStorage__queueServiceUri', value: storage.properties.primaryEndpoints.queue }
        { name: 'AzureWebJobsStorage__tableServiceUri', value: storage.properties.primaryEndpoints.table }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'false' }
        { name: 'ENABLE_ORYX_BUILD', value: 'false' }
        { name: 'WEBSITE_RUN_FROM_PACKAGE', value: '1' }
        { name: 'WEBSITES_CONTAINER_START_TIME_LIMIT', value: '1800' }
        { name: 'DATABASE_URL', value: '@Microsoft.KeyVault(SecretUri=${databaseUrlSecretUri})' }
        { name: 'CREDENTIAL_ENCRYPTION_KEY', value: '@Microsoft.KeyVault(SecretUri=${credentialEncryptionKeySecretUri})' }
        { name: 'DATA_BACKEND', value: 'postgresql' }
        { name: 'PRODUCTION', value: 'true' }
        { name: 'CONTROL_PLANE_ENABLED', value: string(effectiveEnabled) }
        { name: 'GATEWAY_PUBLICATION_WORKER_ENABLED', value: string(effectivePublicationWorkerEnabled) }
        { name: 'GATEWAY_RELEASE_WORKER_ENABLED', value: string(effectiveReleaseWorkerEnabled) }
        { name: 'GATEWAY_APPLICATION_DEFAULT_MONTHLY_TOKEN_LIMIT', value: '100000' }
        { name: 'GATEWAY_APPLICATION_DEFAULT_TOKENS_PER_MINUTE', value: '100000' }
        { name: 'APIM_SUBSCRIPTION_AGENT_MAP', value: string(subscriptionAgentMap) }
        { name: 'AZURE_SUBSCRIPTION_ID', value: subscription().subscriptionId }
        { name: 'APIM_RESOURCE_GROUP', value: apimResourceGroupName }
        { name: 'APIM_SERVICE_NAME', value: apimName }
        { name: 'APIM_API_ID', value: apimApiId }
        { name: 'APIM_CHAT_COMPLETIONS_OPERATION_ID', value: chatCompletionsOperationId }
        { name: 'APIM_RESPONSES_OPERATION_ID', value: responsesOperationId }
        { name: 'APIM_RESPONSES_COMPACT_OPERATION_ID', value: responsesCompactOperationId }
        { name: 'APIM_MESSAGES_OPERATION_ID', value: messagesOperationId }
        { name: 'APIM_COUNT_TOKENS_OPERATION_ID', value: countTokensOperationId }
        { name: 'APIM_MODELS_OPERATION_ID', value: modelsOperationId }
        { name: 'APIM_GATEWAY_URL', value: apimGatewayUrl }
        { name: 'APIM_PROBE_SUBSCRIPTION_KEY', value: '@Microsoft.KeyVault(SecretUri=${apimProbeSubscriptionKeySecretUri})' }
        { name: 'APIM_PROBE_SUBSCRIPTION_ID', value: 'turnstile-publisher-probe' }
        { name: 'APIM_REGRESSION_MODEL_KEY', value: regressionModelKey }
        { name: 'APIM_USAGE_OBSERVER_URL', value: usageObserverUrl }
        { name: 'APIM_USAGE_OBSERVER_KEY_NAMED_VALUE', value: usageObserverKeyNamedValue }
        { name: 'CONTROL_PLANE_LEASE_SECONDS', value: '180' }
        { name: 'CONTROL_PLANE_MAX_ATTEMPTS', value: '30' }
        { name: 'CONTROL_PLANE_PARENT_POLICY_PATH', value: 'policies/foundry-finops-policy.xml' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: applicationInsightsConnectionString }
      ]
    }
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource databaseUrlSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: keyVault
  name: 'database-url'
}

resource credentialEncryptionKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: keyVault
  name: 'credential-encryption-key'
}

resource apimProbeSubscriptionKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: keyVault
  name: 'apim-probe-subscription-key'
}

resource functionStorageBlobOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionApp.id, 'storage-blob-data-owner')
  scope: storage
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
  }
}

resource functionStorageQueueContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionApp.id, 'storage-queue-data-contributor')
  scope: storage
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')
  }
}

resource functionStorageTableContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionApp.id, 'storage-table-data-contributor')
  scope: storage
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3')
  }
}

resource functionDatabaseSecretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(databaseUrlSecret.id, functionApp.id, 'key-vault-secrets-user')
  scope: databaseUrlSecret
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}

resource functionCredentialSecretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(credentialEncryptionKeySecret.id, functionApp.id, 'key-vault-secrets-user')
  scope: credentialEncryptionKeySecret
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}

resource functionProbeSecretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(apimProbeSubscriptionKeySecret.id, functionApp.id, 'key-vault-secrets-user')
  scope: apimProbeSubscriptionKeySecret
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}

output functionName string = functionApp.name
output principalId string = functionApp.identity.principalId
output storageName string = storage.name
