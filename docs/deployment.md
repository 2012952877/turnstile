# Deployment

## Scope

Turnstile infrastructure includes PostgreSQL, Event Hubs, Storage, Key Vault, Application Insights, the Web App, telemetry and control-plane Functions, and APIM configuration. The templates do not create Azure AI Foundry projects or provider model deployments.

## Prepare parameters

Copy `infra/main.parameters.example.json` outside the repository or into an ignored `*.parameters.json` file. Supply secure values from your shell, CI secret store, or Key Vault integration.

## Preview

```bash
az login
az account set --subscription <subscription-id>
az deployment sub what-if \
  --location <deployment-region> \
  --template-file infra/main.bicep \
  --parameters @infra/main.parameters.example.json \
  --parameters postgresAdministratorPassword='<secure-value>' \
               credentialEncryptionKey='<secure-value>' \
               managementApiKey='<secure-value>' \
               apimSubscriptionKey='<secure-value>' \
               apimProbeSubscriptionKey='<secure-value>'
```

Review all creates, updates, deletes, role assignments, network settings, and policy changes before deployment.

## Deploy infrastructure

Replace `what-if` with `create` only after approval. Keep publication workers disabled until the application and control-plane packages are deployed and a probe model has been onboarded.

## Build application packages

```bash
npm --prefix frontend ci
npm --prefix frontend run build
rm -rf .build/api
uv run python -m scripts.stage_deployment api .build/api
```

Install Linux CPython 3.11 wheels into `.build/api/.python_packages/lib/site-packages`, create a deterministic ZIP from `.build/api`, and deploy it with Run-From-Package. Build Function packages from the corresponding staging targets.

## Verify

1. Confirm the infrastructure deployment succeeded.
2. Confirm `/health` returns HTTP 200.
3. Confirm the served frontend asset belongs to the deployed package.
4. Confirm the migration ledger contains `001_initial_schema` once.
5. Verify managed-identity role assignments at their intended resource scopes.
6. Complete the real workflow in [E2E Validation](e2e-validation.md).

## Rollback

Retain the exact previously mounted package and its SHA-256 before each application deployment. A deployment record alone is insufficient; verify the mounted asset and health after rollback.
