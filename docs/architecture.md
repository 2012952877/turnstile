# Architecture

Turnstile separates the inference data plane from management and analytics.

## Components

| Component | Responsibility |
| --- | --- |
| Azure API Management | Authenticates callers, applies access and budget policy, selects an APIM backend pool member, and emits metadata-only usage events. |
| FastAPI Web App | Serves the API and frontend, manages configuration, exposes analytics, and coordinates operator actions. |
| Telemetry Function | Consumes Event Hub records, persists usage, reconciles provider measurements, and updates the budget ledger. |
| Control-plane Function | Applies queued APIM publications and release operations outside the request path. |
| PostgreSQL | Stores configuration, identity, governance, usage, audit, and release state. |
| Table Storage | Holds the low-latency budget projection consumed by APIM. |
| Event Hubs | Decouples inference traffic from telemetry persistence. |
| Key Vault | Stores deployment and provider credentials. |
| Application Insights and Log Analytics | Host operational telemetry and reconciliation inputs. |

## Request flow

1. A client calls the Turnstile APIM endpoint.
2. APIM authenticates the request and evaluates model access and budget state.
3. APIM forwards to a customer-configured provider deployment or native backend pool.
4. APIM emits metadata and provider usage to Event Hubs.
5. The Telemetry Function stores the event in PostgreSQL and updates aggregates.
6. The web application reads PostgreSQL for dashboards and governance workflows.

## Control-plane flow

Connection and model changes are stored as immutable publication intent. The control-plane Function creates a candidate APIM revision, validates it, and promotes it only after probes pass. Gateway releases preserve integrity data and rollback metadata.

## Scope boundary

The deployment creates Turnstile infrastructure. It does not create Azure AI Foundry projects or provider model deployments. Provider resources remain customer-owned and are connected after installation.

APIM native backend pools provide failover, balanced, and weighted distribution. Model Intelligent Router is outside the current release.
