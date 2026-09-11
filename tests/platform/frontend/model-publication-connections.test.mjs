import assert from "node:assert/strict"
import test from "node:test"

import {
  preferredPublicationConnection,
  publicationConnectionAuth,
  publicationConnectionEndpoint,
  publicationConnectionNeedsCredential,
  publicationConnections,
  publicationConnectionTarget,
} from "../../../frontend/src/components/model-management/model-publication-connections.ts"

function gateway(id, overrides = {}) {
  return { id, implementation: "apim", enabled: true, ...overrides }
}

function provider(id, overrides = {}) {
  return { id, enabled: true, ...overrides }
}

function connection(id, gatewayId, overrides = {}) {
  return {
    id, gateway_profile_id: gatewayId, provider_id: "provider-a", enabled: true,
    is_default: false, brand_key: "generic", config: { api_format: "openai_chat" },
    ...overrides,
  }
}

function registry(overrides = {}) {
  return {
    gateways: [gateway("gateway-a"), gateway("gateway-b"), gateway("empty")],
    providers: [provider("provider-a"), provider("provider-b")],
    runtimes: [
      connection("a-first", "gateway-a"),
      connection("a-default", "gateway-a", { is_default: true }),
      connection("a-other-provider", "gateway-a", { provider_id: "provider-b" }),
      connection("b-default", "gateway-b", { is_default: true }),
    ],
    models: [],
    ...overrides,
  }
}

test("connections belong to the selected gateway across enabled providers", () => {
  assert.deepEqual(publicationConnections(registry(), "gateway-a").map((item) => item.id),
    ["a-first", "a-default", "a-other-provider"])
  assert.deepEqual(publicationConnections(registry(), "gateway-b").map((item) => item.id),
    ["b-default"])
})

test("an explicit eligible selection takes precedence over the default and first connection", () => {
  const connections = publicationConnections(registry(), "gateway-a")
  assert.equal(preferredPublicationConnection(connections)?.id, "a-default")
  assert.equal(preferredPublicationConnection(connections, "a-other-provider")?.id, "a-other-provider")
  assert.equal(preferredPublicationConnection(connections.filter((item) => !item.is_default))?.id,
    "a-first")
})

test("switching gateways never retains an out-of-scope connection", () => {
  const connections = publicationConnections(registry(), "gateway-b")
  assert.equal(preferredPublicationConnection(connections, "a-default")?.id, "b-default")
  assert.throws(() => publicationConnectionTarget(registry(), "gateway-b", "a-default"),
    /connection_unavailable/)
})

test("an empty gateway stays empty without borrowing or creating a connection", () => {
  const connections = publicationConnections(registry(), "empty")
  assert.deepEqual(connections, [])
  assert.equal(preferredPublicationConnection(connections, "a-default"), undefined)
  assert.throws(() => publicationConnectionTarget(registry(), "empty", "new-runtime"),
    /connection_unavailable/)
})

test("missing, disabled and non-APIM gateways fail closed", () => {
  for (const overrides of [{ enabled: false }, { implementation: "direct" }]) {
    const data = registry({ gateways: [gateway("gateway-a", overrides)] })
    assert.deepEqual(publicationConnections(data, "gateway-a"), [])
    assert.throws(() => publicationConnectionTarget(data, "gateway-a", "a-default"),
      /connection_unavailable/)
  }
  assert.deepEqual(publicationConnections(registry(), "unknown"), [])
})

test("disabled or missing providers and disabled runtimes are not publishable", () => {
  const data = registry({
    providers: [provider("provider-a"), provider("provider-b", { enabled: false })],
    runtimes: [
      connection("enabled", "gateway-a"),
      connection("disabled", "gateway-a", { enabled: false }),
      connection("disabled-provider", "gateway-a", { provider_id: "provider-b" }),
      connection("missing-provider", "gateway-a", { provider_id: "unknown" }),
    ],
  })
  assert.deepEqual(publicationConnections(data, "gateway-a").map((item) => item.id), ["enabled"])
  for (const id of ["disabled", "disabled-provider", "missing-provider"]) {
    assert.throws(() => publicationConnectionTarget(data, "gateway-a", id), /connection_unavailable/)
  }
})

test("registered zero-model Foundry connections require a managed nonempty Project", () => {
  const config = {
    control_plane_managed: true,
    project_endpoint: "https://example.services.ai.azure.com/api/projects/sample",
    auth_strategy: "named_value_api_key", credential_provisioned: false,
  }
  const data = registry({ runtimes: [
    connection("registered", "gateway-a", { brand_key: "microsoft_foundry", config }),
    connection("unmanaged", "gateway-a", { brand_key: "microsoft_foundry",
      config: { ...config, control_plane_managed: false } }),
    connection("blank-project", "gateway-a", { brand_key: "microsoft_foundry",
      config: { ...config, project_endpoint: " " } }),
    connection("unsupported", "gateway-a", { config: {} }),
  ] })
  assert.deepEqual(publicationConnections(data, "gateway-a").map((item) => item.id), ["registered"])
})

test("existing Bedrock, Databricks and Anthropic runtimes use the same selection path", () => {
  const data = registry({ runtimes: [
    connection("bedrock", "gateway-a", { brand_key: "amazon_bedrock", config: {} }),
    connection("databricks", "gateway-a", { brand_key: "azure_databricks", config: {} }),
    connection("messages", "gateway-a", { config: { api_format: "anthropic_messages" } }),
  ] })
  assert.deepEqual(publicationConnections(data, "gateway-a").map((item) => item.id),
    ["bedrock", "databricks", "messages"])
})

test("runtime authentication overrides provider defaults without exposing configuration", () => {
  assert.equal(publicationConnectionAuth(connection("key", "gateway-a", {
    config: { auth_strategy: "named_value_api_key" },
  }), provider("provider-a", { auth_type: "azure_ad" })), "api_key")
  assert.equal(publicationConnectionAuth(connection("identity", "gateway-a", {
    config: { auth_strategy: "managed_identity" },
  }), provider("provider-a", { auth_type: "api_key" })), "managed_identity")
  assert.equal(publicationConnectionAuth(connection("legacy", "gateway-a")), "connection")
})

test("endpoint summaries omit userinfo, query and fragment and require HTTPS", () => {
  const runtime = connection("redacted", "gateway-a", { config: {
    base_url: "https://sample-user:sample-value@api.example.test/v1/?credential=sample#fragment",
  } })
  assert.equal(publicationConnectionEndpoint(runtime), "https://api.example.test/v1")
  assert.equal(publicationConnectionEndpoint(connection("fallback", "gateway-a", { config: {
    project_endpoint: "invalid", base_url: "http://invalid.test", backend_url: "https://api.example.test/",
  } })), "https://api.example.test")
  assert.equal(publicationConnectionEndpoint(connection("missing", "gateway-a")), null)
})

test("submission carries existing IDs only and cannot carry new endpoint configuration", () => {
  const target = publicationConnectionTarget(registry(), "gateway-a", "a-other-provider")
  assert.deepEqual(target, {
    gateway_profile_id: "gateway-a",
    provider: { existing_id: "provider-b" },
    runtime: { existing_id: "a-other-provider" },
  })
})

test("only a connection explicitly awaiting its first credential receives a key", () => {
  for (const authStrategy of ["named_value_api_key", "named_value_bearer"]) {
    const data = registry({ runtimes: [connection("pending", "gateway-a", { config: {
      api_format: "openai_chat", credential_provisioned: false, auth_strategy: authStrategy,
    } })] })
    assert.equal(publicationConnectionNeedsCredential(data.runtimes[0]), true)
    assert.throws(() => publicationConnectionTarget(data, "gateway-a", "pending", " "),
      /credential_required/)
    assert.deepEqual(publicationConnectionTarget(data, "gateway-a", "pending", " sample-only ").runtime,
      { existing_id: "pending", api_key: "sample-only" })
    data.runtimes[0].config.credential_provisioned = true
    assert.deepEqual(publicationConnectionTarget(data, "gateway-a", "pending", "unused").runtime,
      { existing_id: "pending" })
  }
  assert.equal(publicationConnectionNeedsCredential(connection("identity", "gateway-a", {
    config: { credential_provisioned: false, auth_strategy: "managed_identity" },
  })), false)
  assert.deepEqual(publicationConnectionTarget(registry(), "gateway-b", "b-default", "unused").runtime,
    { existing_id: "b-default" })
})