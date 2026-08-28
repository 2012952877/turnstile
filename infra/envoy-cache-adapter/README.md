# Envoy Cache Adapter Experiment

This branch deploys a Linux App Service that observes provider responses selected by
the existing `turnstile-llm` API. It creates no model, model alias, APIM API,
subscription, or provider route.

```text
Existing client
  -> existing APIM API
  -> isolated Envoy App Service
  -> APIM-selected provider

Envoy access metadata
  -> managed-identity Event Hub REST send
  -> existing telemetry Function
  -> PostgreSQL
  -> existing Turnstile request detail and dashboards
```

The adapter forwards the SSE stream unchanged. It extracts only request attribution
headers and final token usage, strips all Turnstile headers before the provider call,
and never records prompt or completion content.
APIM supplies the existing upstream authority and path; Envoy stores no provider route.
For native APIM Pools, a member backend supplies `x-turnstile-pool-runtime`; the
header-to-metadata filter applies it after the logical entry Runtime and removes it, so
Event Hub records the deployment that actually served the request without exposing the
internal header upstream.
The observer handles OpenAI-compatible SSE, native Anthropic Messages SSE, and
synchronous JSON usage returned by buffered providers such as Bedrock.

The image uses Envoy 1.38.0's work-in-progress `sse_to_metadata` filter. This is an
experiment, not a production recommendation.