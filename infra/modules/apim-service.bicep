targetScope = 'resourceGroup'

@description('Globally unique Azure API Management service name.')
param name string

param location string

@description('Publisher contact shown by Azure API Management.')
param publisherEmail string

@description('Publisher display name shown by Azure API Management.')
param publisherName string

@allowed([
  'Developer'
  'BasicV2'
  'StandardV2'
])
param skuName string

@minValue(1)
param capacity int

resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: name
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: skuName
    capacity: capacity
  }
  properties: {
    publisherEmail: publisherEmail
    publisherName: publisherName
  }
}

output name string = apim.name
output principalId string = apim.identity.principalId
output gatewayUrl string = apim.properties.gatewayUrl
