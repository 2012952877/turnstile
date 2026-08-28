targetScope = 'resourceGroup'

param apimName string

@secure()
param adapterSharedKey string

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = {
  name: apimName
}

resource adapterNamedValue 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'turnstile-envoy-adapter-key'
  properties: {
    displayName: 'turnstile-envoy-adapter-key'
    secret: true
    value: adapterSharedKey
  }
}

output namedValueName string = adapterNamedValue.name
