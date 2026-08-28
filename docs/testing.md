# Testing

## Clean installation

```bash
uv sync --frozen
npm --prefix frontend ci
```

## Required checks

```bash
uv run ruff check backend scripts tests functions/telemetry/function_app.py functions/control_plane/function_app.py
uv run mypy backend scripts tests
uv run pytest -q
npm --prefix frontend run build
az bicep build --file infra/main.bicep
xmllint --noout infra/policies/foundry-finops-policy.xml
npx --yes @redocly/cli@2.38.0 lint contracts/openapi.yaml
git diff --check
```

## Database validation

Apply `migrations/001_initial_schema.up.sql` through `python -m backend.migrate` to a new PostgreSQL 16+ database. Verify that exactly one ledger row is created, then run the command again and verify that no migration is reapplied.

The initial schema contains no users, credentials, provider connections, runtimes, business models, usage events, or customer data.

## Browser validation

Use an authenticated real backend. Check desktop and 390 px mobile viewports, loading and empty states, keyboard focus, hover stability, modal layout, and horizontal overflow. Browser interception or fabricated responses do not count as end-to-end evidence.

## Test boundaries

Unit tests may use in-memory repositories. Integration and E2E results must identify the real deployed environment and resource boundary used for validation.
