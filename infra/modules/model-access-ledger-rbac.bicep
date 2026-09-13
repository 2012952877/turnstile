targetScope = 'resourceGroup'

param ledgerStorageAccountName string
param ledgerTableName string
param apiResourceId string
param apiPrincipalId string

resource ledgerStorage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: ledgerStorageAccountName
}

resource ledgerTableService 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' existing = {
  parent: ledgerStorage
  name: 'default'
}

resource ledgerTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' existing = {
  parent: ledgerTableService
  name: ledgerTableName
}

resource apiLedgerContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(ledgerTable.id, apiResourceId, 'table-data-contributor')
  scope: ledgerTable
  properties: {
    principalId: apiPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
    )
  }
}