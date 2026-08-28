targetScope = 'resourceGroup'

param location string
param appServicePlanName string
param webAppName string
param image string
param acrLoginServer string
param eventHubNamespaceName string
param eventHubName string

@secure()
param adapterSharedKey string

var eventHubsDataSenderRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '2b629674-e913-4c01-ae53-ef4638d8f975'
)

resource plan 'Microsoft.Web/serverfarms@2024-11-01' = {
  name: appServicePlanName
  location: location
  kind: 'linux'
  sku: {
    name: 'B1'
    tier: 'Basic'
    size: 'B1'
    capacity: 1
  }
  properties: {
    reserved: true
  }
}

resource webApp 'Microsoft.Web/sites@2024-11-01' = {
  name: webAppName
  location: location
  kind: 'app,linux,container'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    publicNetworkAccess: 'Enabled'
    siteConfig: {
      linuxFxVersion: 'DOCKER|${image}'
      acrUseManagedIdentityCreds: true
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      healthCheckPath: '/health'
      appSettings: [
        { name: 'WEBSITES_PORT', value: '8080' }
        { name: 'DOCKER_REGISTRY_SERVER_URL', value: 'https://${acrLoginServer}' }
        { name: 'EVENT_HUB_FQDN', value: '${eventHubNamespaceName}.servicebus.windows.net' }
        { name: 'EVENT_HUB_NAME', value: eventHubName }
        { name: 'ADAPTER_SHARED_KEY', value: adapterSharedKey }
      ]
    }
  }
}

resource eventHubNamespace 'Microsoft.EventHub/namespaces@2024-01-01' existing = {
  name: eventHubNamespaceName
}

resource usageEventHub 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' existing = {
  parent: eventHubNamespace
  name: eventHubName
}

resource eventHubSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: usageEventHub
  name: guid(usageEventHub.id, webApp.id, 'envoy-cache-adapter-event-hub-sender')
  properties: {
    principalId: webApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: eventHubsDataSenderRoleDefinitionId
  }
}

output webAppUrl string = 'https://${webApp.properties.defaultHostName}'
output principalId string = webApp.identity.principalId
