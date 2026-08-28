# Deployment

## Scope

Turnstile infrastructure includes PostgreSQL, Event Hubs, Storage, Key Vault, Application Insights, the Web App, telemetry and control-plane Functions, and APIM configuration. The templates do not create Azure AI Foundry projects or provider model deployments.

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

Set the resource prefix, resource group, Azure and PostgreSQL regions, APIM publisher email, and `bootstrapOwnerEmail`. `entraClientId` and `entraAllowedEmailDomains` are optional; password Owner login works without Entra.

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
5. Deploy all three Run-From-Package artifacts.
6. Provision a Basic ACR and observer App Service, build the image remotely, and restart the observer.
7. Run another no-delete what-if and enable the workers with the observer URL and APIM Named Value.
8. Verify API health, Function indexing, and real password Owner login.

Generated packages and deployment state remain under `.turnstile/deployments`. The script prints and stores only non-secret Azure outputs.

## Initial Owner

The deployment command reads the password interactively and never sends plaintext to ARM. It deploys only the scrypt hash. API startup applies the schema and atomically creates the Owner only when `app_user` is empty; restarts and reruns do not reset the account.

To rotate a password later, run the existing account command from an authorized application execution context:

```bash
python -m backend.accounts <owner-email> --role owner
```

## Customer Foundry onboarding

The deployment output includes the APIM managed-identity principal ID. Turnstile does not grant it access to customer resources and does not create a Foundry project.

Use the authenticated web console to add the existing Foundry Project Endpoint and model deployment. If the candidate probe lacks access, the publication pauses and displays the exact principal, resource endpoint, and `Cognitive Services User` role. An Azure user authorized on that Foundry account grants the role, then resumes the publication in the same dialog.

Creating connections or models through direct API calls does not count as frontend E2E evidence.

## Verify

1. Confirm the infrastructure deployment succeeded.
2. Confirm `/health` returns HTTP 200.
3. Confirm the served frontend asset belongs to the deployed package.
4. Confirm the migration ledger contains `001_initial_schema` once.
5. Confirm the bootstrapped Owner can sign in with a password.
6. Verify managed-identity role assignments at their intended resource scopes.
7. Complete the real workflow in [E2E Validation](e2e-validation.md).

## Reruns and recovery

The command is idempotent for the same parameter and state files. Keep the state file available: generating replacement platform keys during a rerun can invalidate database credentials, encrypted provider credentials, APIM subscriptions, and observer authentication.

If a phase fails, correct the reported cause and run the same command again. Every infrastructure phase repeats what-if and still refuses Delete changes. Use `--yes` only in controlled automation after separately reviewing `scripts.deploy plan`; it never bypasses the no-delete gate.

## Rollback

Retain the exact previously mounted package and its SHA-256 before each application deployment. A deployment record alone is insufficient; verify the mounted asset and health after rollback.
