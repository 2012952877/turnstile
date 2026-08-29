targetScope = 'resourceGroup'

param apimName string
param adapterKeyNamedValueName string = 'turnstile-envoy-adapter-key'

@secure()
param adapterSharedKey string

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = {
  name: apimName
}

resource adapterNamedValue 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: adapterKeyNamedValueName
  properties: {
    displayName: adapterKeyNamedValueName
    secret: true
    value: adapterSharedKey
  }
}

output namedValueName string = adapterNamedValue.name
