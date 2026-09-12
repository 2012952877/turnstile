# Deployment

## Scope

Turnstile infrastructure includes PostgreSQL, Event Hubs, Storage, Key Vault, Application Insights, the Web App, telemetry and control-plane Functions, and APIM configuration. FastAPI and the observer each have a dedicated App Service Plan. The telemetry and control-plane Functions each have a separate Flex Consumption plan, deployment container, and VNet subnet. The templates do not create Azure AI Foundry projects or provider model deployments.

The API, telemetry and control-plane Functions, observer, and budget ledger require public service endpoints for package deployment or runtime traffic. They explicitly enable public network access and carry the resource-level `SecurityControl=Ignore` tag so an organization-level network Modify policy does not silently disable them. The exemption is not applied at resource-group scope; platform Storage and Key Vault resources designed for private-link access remain private.

## Prerequisites

- Azure CLI with an authenticated subscription context
- `uv` and Python 3.11 or newer
- Node.js and npm versions accepted by `frontend/package.json`
- Permission to create the resource group and included resources
- Permission to create role assignments; use Owner, or Contributor plus User Access Administrator, at the target scope

Docker is not required. The observer image is built remotely by Azure Container Registry.

## Prepare public parameters

Copy the example into the ignored `.turnstile` directory:

```bash
mkdir -p .turnstile
cp infra/main.parameters.example.json .turnstile/main.parameters.json
```

Set the resource prefix, resource group, Azure and PostgreSQL regions, APIM publisher email, and `bootstrapOwnerEmail`. New deployments provision a capacity-one `StandardV2` APIM by default. `entraClientId` and `entraAllowedEmailDomains` are optional; password Owner login works without Entra.

To run a clean end-to-end deployment without provisioning another APIM service, set all four `existingApimName`, `existingApimResourceGroupName`, `existingApimPrincipalId`, and `existingApimGatewayUrl` values. PostgreSQL and every other platform resource are still created from scratch. The deployment derives environment-unique API, path, product, subscription, logger, diagnostic-setting, and observer Named Value identifiers from `resourcePrefix`, so the shared APIM configuration does not overwrite another Turnstile environment.

Do not add passwords, keys, hashes, or connection strings to this file. The deployment command rejects secure parameter names in the public file.

Interactive deployment prompts for the initial Owner's chosen password. For automation, copy `infra/owner.credentials.example.json` to `.turnstile/owner.credentials.json`, use the same email as `bootstrapOwnerEmail`, choose the password, run `chmod 600`, and pass `--owner-credentials .turnstile/owner.credentials.json`. The plaintext is read only in process memory; state and ARM receive only its scrypt hash.

## Preview

```bash
az login
uv sync --frozen
uv run python -m scripts.deploy plan \
  --subscription <subscription-id> \
  --parameters .turnstile/main.parameters.json
```

The first preview asks for the initial Owner password twice and stores only its scrypt hash. It also creates independent random platform secrets in `.turnstile/deployments/<resource-group>.json` with mode `0600`. Repeated previews and deployments reuse that state.

Before creating deployment state, the command verifies that the selected region supports Flex Consumption, that `Microsoft.App` is registered, and that the selected PostgreSQL region supports PostgreSQL 16 with the configured SKU and availability zone. If either service is unavailable, choose another location and run the preview again.

Review all creates, updates, unsupported previews, role assignments, and network settings. The script refuses every what-if containing a Delete change.

## Deploy

```bash
uv run python -m scripts.deploy deploy \
  --subscription <subscription-id> \
  --parameters .turnstile/main.parameters.json
```

The command runs these phases in order:

1. Re-run the platform what-if and require the exact confirmation `deploy`.
2. Provision the platform with publication and release workers disabled.
3. Build the frontend with the configured public Entra client ID.
4. Install exact Linux x86-64/Python 3.11 dependencies and create deterministic API, telemetry Function, and control-plane Function ZIPs.
5. Deploy the API and Function packages with short-lived Microsoft Entra authentication. Flex Functions use One Deploy, and SCM basic publishing credentials remain disabled.
6. Provision a Basic ACR and observer App Service on its own Premium v3 plan, build the image remotely, and restart the observer. The default is one `P0v3` worker; set `observerPlanSkuName` and `observerPlanWorkerCount` in the deployment parameters when additional compute or horizontal capacity is required.
7. Run another no-delete what-if and enable the workers with the observer URL and APIM Named Value.
8. Verify API health, Function indexing, and real password Owner login.

Generated packages and deployment state remain under `.turnstile/deployments`. The script prints and stores only non-secret Azure outputs.

## Initial Owner

The deployment command reads the password interactively and never sends plaintext to ARM. It deploys only the scrypt hash. API startup applies the schema and atomically creates the Owner only when `app_user` is empty; restarts and reruns do not reset the account. Enabled Owner accounts are immediately listed in People under the default AI Platform department so the bootstrap Owner can assign model access before generating gateway traffic. Member department placement still comes from attributed gateway usage.

To rotate a password later, run the existing account command from an authorized application execution context:

```bash
python -m backend.accounts <owner-email> --role owner
```

## Customer Foundry onboarding

The deployment output includes the APIM managed-identity principal ID. Turnstile does not grant it access to customer resources and does not create a Foundry project.

Use the authenticated web console to add the existing Foundry Project Endpoint and model deployment. The user does not enter model capabilities: Turnstile derives them from the provider protocol, including `chat`, `tools`, and `streaming` for OpenAI-family Foundry deployments. If the candidate probe lacks access, the publication pauses and displays the exact principal, resource endpoint, and `Cognitive Services User` role. An Azure user authorized on that Foundry account grants the role, then resumes the publication in the same dialog. Model access remains explicit; assign the new model to the intended people before using Invocation Test.

Creating connections or models through direct API calls does not count as frontend E2E evidence.

### Image and evidence upgrade boundary

Apply pending migrations `004_apim_usage_identity_guard`, `005_billable_request_lifecycle` and `006_versioned_budget_evidence` through the same authorized migration entry point before running the new packages. Do not edit or replay the earlier migrations. The upgrade adds guarded APIM attempt identity, a durable billable-request journal and versioned budget evidence without rewriting historical usage. Drain old consumers before introducing the new identity guard. PostgreSQL concurrency and upgrade safety require the database checks in [Testing](testing.md).

The API and Control-plane packages both require the pinned Pillow dependency for full image validation. The Control-plane artifact includes the public canonical parent policy at `policies/foundry-finops-policy.xml`; publication and image rollback read it through `CONTROL_PLANE_PARENT_POLICY_PATH`. The publisher verifies the live policy and its readback, and refuses incompatible image policies instead of transforming an unknown live template. Deploy the reviewed public parent-policy change through the normal infrastructure what-if boundary before enabling image generation.

Keep `IMAGE_GENERATION_ENABLED=false` and the evidence-v2 cutoff unset during the code and migration rollout. Enabling images, authorizing paid publication probes and scheduling the future evidence cutoff are separate operational decisions. This upgrade does not provision a Foundry account or model, modify an upstream deployment, or grant new provider roles.

## Verify

1. Confirm the infrastructure deployment succeeded.
2. Confirm `/health` returns HTTP 200.
3. Confirm the served frontend asset belongs to the deployed package.
4. Confirm the migration ledger contains each migration listed in [Testing](testing.md) once, with unchanged checksums for previously applied files.
5. Confirm the bootstrapped Owner can sign in with a password.
6. Verify managed-identity role assignments at their intended resource scopes.
7. Complete the real workflow in [E2E Validation](e2e-validation.md).

## Reruns and recovery

The command is idempotent for the same parameter and state files. Keep the state file available: generating replacement platform keys during a rerun can invalidate database credentials, encrypted provider credentials, APIM subscriptions, and observer authentication.

If a phase fails, correct the reported cause and run the same command again. Every infrastructure phase repeats what-if and still refuses Delete changes. Use `--yes` only in controlled automation after separately reviewing `scripts.deploy plan`; it never bypasses the no-delete gate.

## Rollback

Retain the exact previously mounted package and its SHA-256 before each application deployment. A deployment record alone is insufficient; verify the mounted asset and health after rollback.
