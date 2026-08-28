# Troubleshooting

## API fails at startup

- Confirm `DATABASE_URL` is set and reachable over TLS.
- Run `uv run python -m backend.migrate` manually and inspect the first error.
- Confirm `frontend/dist/index.html` exists when `PRODUCTION=true`.

## Microsoft sign-in is unavailable

- Set both `ENTRA_CLIENT_ID` and `VITE_ENTRA_CLIENT_ID` to the same SPA application ID.
- Add every browser origin as an Entra redirect URI.
- Configure `ENTRA_ALLOWED_EMAIL_DOMAINS` as a JSON array.
- Rebuild the frontend after changing `VITE_` variables.

## APIM publication does not advance

- Keep the worker disabled until its managed identity and APIM role are deployed.
- Confirm the probe subscription and `APIM_REGRESSION_MODEL_KEY` refer to an onboarded model.
- Inspect release state before retrying; do not create parallel publications for one gateway.

## Telemetry is missing

- Verify APIM can send to Event Hubs and the Telemetry Function can receive from it.
- Confirm the Function uses managed-identity storage settings.
- Check correlation IDs across APIM, Event Hubs, Function logs, and PostgreSQL.

## App Service deployment succeeds but old assets remain

A successful OneDeploy record does not prove the Run-From-Package mount changed. Read the served `index.html`, compare its asset name, and inspect container startup logs. Avoid repeated restarts without mount or startup evidence.

## Frontend cannot call the API locally

Set `API_PROXY_TARGET` in `frontend/.env.local`, restart Vite, and confirm `/health` through `http://localhost:5173/health`.
