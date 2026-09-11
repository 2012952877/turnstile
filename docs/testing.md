# Testing

## Clean installation

```bash
uv sync --frozen
npm --prefix frontend ci
```

## Required checks

```bash
uv run ruff check backend turnstile_core scripts tests functions/telemetry/function_app.py functions/control_plane/function_app.py
uv run mypy backend turnstile_core scripts tests
uv run pytest -q
npm --prefix frontend run build
az bicep build --file infra/main.bicep
xmllint --noout infra/policies/foundry-finops-policy.xml
npx --yes @redocly/cli@2.38.0 lint contracts/openapi.yaml
git diff --check
```

## Database validation

For a clean installation, run `uv run python -m backend.migrate` against an authorized new PostgreSQL 16+ database. Verify one `schema_migration` row per numbered migration (currently `001_initial_schema`, `002_apim_request_attempt_identity`, and `003_budget_reservation_finalization`), then run the command again and verify that no migration is reapplied.

The initial schema contains no users, credentials, provider connections, runtimes, business models, usage events, or customer data.

For an existing installation, run the same migration command to apply only pending upgrades. Verify that the initial migration checksum and existing usage rows are unchanged. Migration `002` replaces the unique caller-request index with a non-unique `(request_id, ts DESC)` index; it does not rewrite historical usage. Apply it before running the updated telemetry consumer, and drain older telemetry consumers before enabling the new version. Do not overlap consumers that use the old and new identity rules. Index replacement takes a table lock, so schedule the upgrade for an appropriate maintenance window.

Request-attempt validation must prove that two APIM attempts with the same `request_id` and different `correlation_id` values both persist, while redelivery of one attempt is idempotent. Replay a pre-upgrade event whose stored primary key differs from its correlation ID: the existing primary key and Application attribution must remain unchanged, an estimated record may gain exact usage, and an already exact record must not be charged again. Detail lookup prefers an exact `correlation_id`; a legacy `request_id` selects the latest matching APIM attempt. Verify both lookups and confirm that Copilot records remain excluded. Isolated unit and migration-source tests do not substitute for this database validation.

### Ledger upgrade validation

Apply `003_budget_reservation_finalization` before deploying the updated API and Telemetry packages. Verify that the initial two migration checksums and historical request rows are unchanged. The new evidence is append-only and the Application ledger snapshot is updated atomically; older timer snapshots must not replace newer ones.

In a separately authorized PostgreSQL/Table/Log Analytics environment, verify exact recovery, terminal-zero failures, silent reservations past the grace period, unavailable and partial log results, both Person and Application scopes, retired applications, and old-month partitions. Upper-bound finalization must leave the original `R.Reserved` amount unchanged. Project confirmed usage before deleting settled reservations, including usage that crossed a month boundary. A late unmeasured event must not erase recovered exact usage; later final usage must replace it without double counting. Re-run after interrupted Table marking and deletion to prove convergence.

For API and browser checks, distinguish a missing snapshot from zero pending usage. Verify that available Tokens subtract both pending reservations and finalized upper bounds. Recovery records have no model pricing or cache classification; they must not invent prices, latency or per-user cache measurements. Unit tests and SQL source checks do not establish live data-plane or migration correctness.

## Browser validation

Use an authenticated real backend. Check desktop and 390 px mobile viewports, loading and empty states, keyboard focus, hover stability, modal layout, and horizontal overflow. Browser interception or fabricated responses do not count as end-to-end evidence.

## Test boundaries

Unit tests may use in-memory repositories. Integration and E2E results must identify the real deployed environment and resource boundary used for validation.
