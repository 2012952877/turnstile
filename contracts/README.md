# Contracts

`openapi.yaml` is the canonical HTTP API contract. It indexes domain-owned path and schema files under `openapi/` and includes the metadata-only Event Hub contract.

Validate from the repository root:

```bash
npx --yes @redocly/cli@2.38.0 lint contracts/openapi.yaml
```

Runtime Pydantic models and the reviewed OpenAPI contract must evolve together. Contract tests reject stale documented routes and missing Assistant endpoints.

The Event Hub event intentionally excludes prompts and completions. It carries attribution, request and correlation identifiers, status, latency, provider usage, and pricing metadata only.

The PostgreSQL installation source is `migrations/001_initial_schema.up.sql`; there is no second schema snapshot in this repository.
