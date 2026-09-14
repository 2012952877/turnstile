targetScope = 'resourceGroup'

@description('Names of existing API Web Apps in this resource group that save model permissions.')
@minLength(1)
param apiNames string[]

@description('Existing App Settings keyed by API name, read immediately before deployment.')
@secure()
param currentApiSettings object

@description('Resource group containing the existing authoritative budget ledger.')
param ledgerResourceGroupName string

@description('Existing budget ledger storage account. No account or table is created.')
param ledgerStorageAccountName string

@description('Existing budget ledger table shared by APIM and the Telemetry Function.')
param ledgerTableName string

resource ledgerStorage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  scope: resourceGroup(ledgerResourceGroupName)
  name: ledgerStorageAccountName
}

resource apis 'Microsoft.Web/sites@2024-11-01' existing = [for apiName in apiNames: {
  name: apiName
}]

module ledgerAccess 'modules/model-access-ledger-rbac.bicep' = [for (apiName, index) in apiNames: {
  name: 'model-access-${uniqueString(resourceGroup().id, apiName, ledgerStorage.id, ledgerTableName)}'
  scope: resourceGroup(ledgerResourceGroupName)
  params: {
    ledgerStorageAccountName: ledgerStorageAccountName
    ledgerTableName: ledgerTableName
    apiResourceId: apis[index].id
    apiPrincipalId: apis[index].identity.principalId
  }
}]

resource apiSettings 'Microsoft.Web/sites/config@2024-11-01' = [for (apiName, index) in apiNames: {
  parent: apis[index]
  name: 'appsettings'
  properties: union(currentApiSettings[apiName], {
    LEDGER_SYNC_ENABLED: 'true'
    LEDGER_TABLE_ENDPOINT: ledgerStorage.properties.primaryEndpoints.table
    LEDGER_TABLE_NAME: ledgerTableName
  })
  dependsOn: [
    ledgerAccess[index]
  ]
}]
