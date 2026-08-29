# Azure infrastructure

`main.bicep` is a subscription-scope clean-install template for Turnstile. It creates:

- a dedicated resource group;
- Azure API Management with a system-assigned identity;
- Azure Database for PostgreSQL Flexible Server;
- Event Hubs namespace and usage event hub;
- Key Vault;
- Application Insights and Log Analytics;
- a Linux Web App for the API and frontend on its own App Service Plan;
- telemetry and control-plane Function Apps, each on its own Flex Consumption plan;
- a separately deployed observer on its own App Service Plan;
- a dedicated Entra-only Table Storage budget ledger;
- optional control-plane Function and least-privilege APIM roles.

It does not create Azure AI Foundry projects, provider accounts, or model deployments. Add customer-owned provider connections and models after Turnstile is healthy.

## Validate

```bash
az bicep build --file infra/main.bicep
```

## Configure

Copy `main.parameters.example.json` to an ignored `*.parameters.json` file and replace the example publisher and organization values. Supply these secure parameters through your shell or CI secret store:

- `postgresAdministratorPassword`
- `credentialEncryptionKey`
- `managementApiKey`
- `apimSubscriptionKey`
- `apimProbeSubscriptionKey`

Do not store those values in a checked-in parameter file.

To reuse an existing APIM service while creating the rest of the environment from scratch, set `existingApimName`, `existingApimResourceGroupName`, `existingApimPrincipalId`, and `existingApimGatewayUrl`. Leave all four empty to provision a new capacity-one `StandardV2` APIM service. Shared APIM resources are isolated by `resourcePrefix`.

## Preview and deploy

```bash
az deployment sub what-if \
  --location <deployment-region> \
  --template-file infra/main.bicep \
  --parameters @infra/main.parameters.json \
  --parameters postgresAdministratorPassword='<secure-value>' \
               credentialEncryptionKey='<secure-value>' \
               managementApiKey='<secure-value>' \
               apimSubscriptionKey='<secure-value>' \
               apimProbeSubscriptionKey='<secure-value>'
```

Review the preview before using `az deployment sub create` with the same arguments. Initial deployment leaves publication and key-management workers disabled.

After deploying application packages, onboard an existing provider project and model through Turnstile. Only then configure a publication probe model and enable the control-plane workers.
