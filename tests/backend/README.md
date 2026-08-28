# Backend test partition

This partition owns unit and integration behavior internal to the reusable `backend` package.

| Directory | Implementation under test |
|---|---|
| `assistant/` | Assistant loop, tools, conversations, reports, and settings |
| `governance/` | Budget and model-access business rules |
| `model_platform/` | Runtime adapters/services, publication compiler/worker, and control plane |
| `telemetry/` | Event ingestion, Copilot import, reconciliation, and ledger synchronization |
| `persistence/` | Repository selection, pooling, and identity caches |
| `runtime/` | Operational migration entry points |

Cross-runtime API, source, deployment, and infrastructure contracts belong under
`tests/platform/`. The entire repository test suite is excluded from deployment artifacts.

```bash
uv run pytest -q tests/backend
```