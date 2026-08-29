targetScope = 'resourceGroup'

param apimName string
param apiPrincipalIds array

var roleNameSuffix = uniqueString(resourceGroup().id)

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = {
  name: apimName
}

resource keyOperatorRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'turnstile-apim-subscription-key-operator')
  properties: {
    roleName: 'Turnstile APIM Subscription Key Operator ${roleNameSuffix}'
    description: 'Allows the Turnstile API to reveal and rotate keys for governed APIM subscriptions.'
    type: 'CustomRole'
    permissions: [
      {
        actions: [
          'Microsoft.ApiManagement/service/subscriptions/read'
          'Microsoft.ApiManagement/service/subscriptions/listSecrets/action'
          'Microsoft.ApiManagement/service/subscriptions/regeneratePrimaryKey/action'
          'Microsoft.ApiManagement/service/subscriptions/regenerateSecondaryKey/action'
        ]
        notActions: []
        dataActions: []
        notDataActions: []
      }
    ]
    assignableScopes: [
      resourceGroup().id
    ]
  }
}

resource apiKeyOperators 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for apiPrincipalId in apiPrincipalIds: {
  name: guid(apim.id, apiPrincipalId, 'turnstile-apim-subscription-key-operator')
  scope: apim
  properties: {
    principalId: apiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyOperatorRole.id
  }
}]
