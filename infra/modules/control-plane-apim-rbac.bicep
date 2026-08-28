targetScope = 'resourceGroup'

param apimName string
param controlPlanePrincipalId string

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = {
  name: apimName
}

resource publisherRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'finops-apim-publisher')
  properties: {
    roleName: 'FinOps APIM Publisher'
    description: 'Publishes FinOps API resources and provisions governed APIM subscriptions.'
    type: 'CustomRole'
    permissions: [
      {
        actions: [
          'Microsoft.ApiManagement/service/apis/operations/policies/read'
          'Microsoft.ApiManagement/service/apis/operations/policies/write'
          'Microsoft.ApiManagement/service/apis/operations/read'
          'Microsoft.ApiManagement/service/apis/policies/read'
          'Microsoft.ApiManagement/service/apis/policies/write'
          'Microsoft.ApiManagement/service/apis/read'
          'Microsoft.ApiManagement/service/apis/releases/read'
          'Microsoft.ApiManagement/service/apis/releases/write'
          'Microsoft.ApiManagement/service/apis/write'
          'Microsoft.ApiManagement/service/backends/read'
          'Microsoft.ApiManagement/service/backends/write'
          'Microsoft.ApiManagement/service/namedValues/read'
          'Microsoft.ApiManagement/service/namedValues/write'
          'Microsoft.ApiManagement/service/policies/read'
          'Microsoft.ApiManagement/service/policyFragments/read'
          'Microsoft.ApiManagement/service/products/apiLinks/read'
          'Microsoft.ApiManagement/service/products/apis/read'
          'Microsoft.ApiManagement/service/products/policies/read'
          'Microsoft.ApiManagement/service/products/policy/read'
          'Microsoft.ApiManagement/service/products/read'
          'Microsoft.ApiManagement/service/read'
          'Microsoft.ApiManagement/service/subscriptions/read'
          'Microsoft.ApiManagement/service/subscriptions/write'
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

resource functionApimPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(apim.id, controlPlanePrincipalId, 'finops-apim-publisher')
  scope: apim
  properties: {
    principalId: controlPlanePrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: publisherRole.id
  }
}
