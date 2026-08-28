# Test suite

All Turnstile tests live under this single collection root. The first directory below the root
identifies the behavior owner, so contributors do not need to choose between competing test trees.

| Directory | Behavior under test |
|---|---|
| `backend/` | Backend services, adapters, parsers, persistence, workers, and runtime entry points |
| `platform/` | Public API, architecture, contracts, deployment, frontend, and infrastructure |
| `support/` | Repository paths and helpers shared by the test partitions |

Backend tests may use implementation-level fakes and fixtures. Platform tests verify durable
cross-runtime boundaries and may inspect canonical source, contracts, and deployment artifacts.

```bash
uv run pytest -q tests/backend
uv run pytest -q tests/platform
uv run pytest -q
```