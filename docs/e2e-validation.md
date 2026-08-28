# End-to-End Validation

Use this checklist for each release. Record resource identifiers and request IDs in a private deployment record, not in the repository.

## Environment

- [ ] New or intentionally empty Turnstile PostgreSQL database
- [ ] Fresh dependency installation from lockfiles
- [ ] Infrastructure preview reviewed
- [ ] Turnstile resources deployed successfully
- [ ] No Foundry project or provider model created by the deployment

## Platform bootstrap

- [ ] Apply `001_initial_schema`
- [ ] Re-run the migration command and confirm zero pending migrations
- [ ] Start the API and frontend
- [ ] Sign in as Owner
- [ ] Confirm health, authentication, registry, and empty-state pages

## Customer-owned model onboarding

- [ ] Add a connection to an existing Foundry project
- [ ] Add a model that references an existing provider deployment
- [ ] Configure an APIM native backend pool if multiple equivalent runtimes exist
- [ ] Publish and verify the gateway release
- [ ] Make one small attributed invocation
- [ ] Confirm status, runtime, model, token usage, cost, and correlation ID in Request Trace
- [ ] Confirm no prompt or completion content is persisted

## Governance

- [ ] Configure an application budget
- [ ] Configure model access
- [ ] Verify an allowed request
- [ ] Verify a denied request with the expected reason
- [ ] Verify Owner-only application key actions without logging a key

## Browser acceptance

- [ ] Desktop screenshot reviewed
- [ ] 390 px mobile screenshot reviewed
- [ ] No horizontal overflow
- [ ] Loading, empty, error, disabled, hover, and focus states reviewed

## Result record

| Field | Value |
| --- | --- |
| Date | 2026-08-28 |
| Commit | Initial repository commit |
| Clean dependency install | Passed from an exported Git index tree with `uv sync --frozen` and `npm ci` |
| Automated validation | 578 tests passed in both working and clean trees; Ruff, mypy, frontend build, Bicep, OpenAPI, and APIM XML passed |
| Local process smoke | Clean-tree API, Vite frontend, and Vite `/health` proxy returned HTTP 200 in explicit demo mode; not persistence evidence |
| Infrastructure preview | Subscription what-if passed: 62 Create, 2 Unsupported, 0 Modify/Delete; unsupported types were Storage Account and Event Hubs |
| Infrastructure deployment | Not run; creating the empty platform requires explicit target and cost approval |
| Application deployment | Not run against a new empty platform |
| Connection/model/pool validation | Not run against a new empty platform |
| Known limitations | See root README |

Do not mark a row passed without evidence from the real configured environment. Local unit tests and mocks are not E2E evidence.
