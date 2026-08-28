targetScope = 'subscription'

param resourceGroupName string
param apimResourceGroupName string = resourceGroupName
param location string
param appServicePlanName string
param webAppName string
param acrName string
param imageRepository string = 'turnstile/envoy-cache-adapter'
param imageTag string
param eventHubNamespaceName string
param eventHubName string = 'token-usage'
param apimName string

@secure()
param adapterSharedKey string

var acrLoginServer = '${acrName}.azurecr.io'
var image = '${acrLoginServer}/${imageRepository}:${imageTag}'

resource platformResourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' existing = {
  name: resourceGroupName
}

resource apimResourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' existing = {
  name: apimResourceGroupName
}

module registry 'br/public:avm/res/container-registry/registry:0.13.0' = {
  name: 'envoy-cache-adapter-registry'
  scope: apimResourceGroup
  params: {
    name: acrName
    location: location
    acrSku: 'Basic'
    acrAdminUserEnabled: false
  }
}

module app 'app.bicep' = {
  scope: platformResourceGroup
  name: 'envoy-cache-adapter-app'
  params: {
    location: location
    appServicePlanName: appServicePlanName
    webAppName: webAppName
    image: image
    acrLoginServer: acrLoginServer
    eventHubNamespaceName: eventHubNamespaceName
    eventHubName: eventHubName
    adapterSharedKey: adapterSharedKey
  }
}

module acrRole 'acr-role.bicep' = {
  scope: apimResourceGroup
  name: 'envoy-cache-adapter-acr-role'
  dependsOn: [
    registry
  ]
  params: {
    acrName: acrName
    principalId: app.outputs.principalId
  }
}

module apimIntegration 'apim.bicep' = {
  scope: apimResourceGroup
  name: 'envoy-cache-adapter-apim'
  params: {
    apimName: apimName
    adapterSharedKey: adapterSharedKey
  }
}

output webAppUrl string = app.outputs.webAppUrl
output webAppName string = webAppName
output webAppPrincipalId string = app.outputs.principalId
output adapterKeyNamedValueName string = apimIntegration.outputs.namedValueName
output acrName string = acrName
output image string = image
