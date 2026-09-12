import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire, stripTypeScriptTypes } from "node:module"
import test from "node:test"
import { setImmediate as nextTurn } from "node:timers/promises"
import { fileURLToPath } from "node:url"
import { runInNewContext } from "node:vm"

import { generatedImageBlob, imageDownloadFilename } from "../../../frontend/src/data-sources/apim/image-generation.ts"

const apiSource = readFileSync(new URL("../../../frontend/src/data-sources/apim/api.ts", import.meta.url), "utf8")
const normalizationStart = apiSource.indexOf("const LEGACY_CLOUD_CODE_PROVIDER_ID")
const normalizationEnd = apiSource.indexOf("const normalizeGatewayPublication", normalizationStart)
assert.ok(normalizationStart >= 0 && normalizationEnd > normalizationStart)
const normalizeRegistry = runInNewContext(`${stripTypeScriptTypes(apiSource.slice(normalizationStart, normalizationEnd))}\nnormalizeRegistry`)
const emptyRegistry = { gateways: [], providers: [], runtimes: [], models: [] }

const frontendRequire = createRequire(new URL("../../../frontend/package.json", import.meta.url))
const { rolldown } = await import(frontendRequire.resolve("rolldown"))
const invocationEntry = fileURLToPath(new URL("../../../frontend/src/data-sources/apim/pages/dashboard-invocation.tsx", import.meta.url))
const invocationBundle = await rolldown({ input: invocationEntry, external: id => id !== invocationEntry, transform: { jsx: { runtime: "automatic" } }, treeshake: false })
let invocationComponent
try { invocationComponent = (await invocationBundle.generate({ format: "cjs" })).output[0].code }
finally { await invocationBundle.close() }

for (const [enabled, version, allowed] of [[true, 4, true], [false, 4, false], [undefined, 4, false], [true, 3, false]]) {
  test(`invocation component consumes normalized image capability ${enabled}/${version}`, async () => {
    const registry = normalizeRegistry({
      ...emptyRegistry, image_generation_supported: enabled, image_configuration_schema_version: version,
      runtimes: [{ id: "runtime", name: "Unit runtime", config: {} }],
      models: [{ id: "image", runtime_id: "runtime", model_key: "unit-image", display_name: "Unit image", enabled: true, capabilities: ["image_generation"], image_profile: { version: 4 } }],
    })
    const slots = []
    let cursor = 0
    const requests = []
    const hooks = {
      useState(initial) {
        const index = cursor++
        if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial
        return [slots[index], value => { slots[index] = typeof value === "function" ? value(slots[index]) : value }]
      },
      useEffect() {}, useMemo: callback => callback(),
    }
    const imports = {
      react: hooks, "react/jsx-runtime": frontendRequire("react/jsx-runtime"),
      "@tanstack/react-query": { useQuery: () => ({ data: registry }), useQueryClient: () => ({}) },
      "../../../providers/auth-provider": { useAuth: () => ({ user: { email: "unit@example.com", name: "Unit", role: "member" } }) },
      "../queries": { finopsQueries: { registry: () => ({}) }, invalidateFinOps() {} },
      "../api": { dataSource: { generateImage: async request => { requests.push(request); return { request_id: "unit-response" } } } },
    }
    const exports = {}
    runInNewContext(invocationComponent, { exports, crypto: { randomUUID: () => "unit-run" }, require: name => imports[name] ?? new Proxy({}, { get: (_target, key) => key }) })
    const entities = { organizations: [{ id: "org", name: "Org" }], departments: [{ id: "department", name: "Department" }], projects: [{ id: "project", name: "Project", parent_id: "department" }], agents: [{ id: "agent", name: "Agent", parent_id: "project" }], users: [] }
    const nodes = value => Array.isArray(value) ? value.flatMap(nodes) : value && typeof value === "object" ? [value, ...nodes(value.props?.children)] : []
    const render = () => { cursor = 0; return exports.AgentInvocation({ entities }) }
    const find = predicate => nodes(render()).find(predicate)
    find(node => node.type === "button" && node.props.children?.includes("图像")).props.onClick()
    find(node => node.type === "Select").props.onValueChange("model:image")
    find(node => node.type === "textarea").props.onChange({ target: { value: "unit prompt" } })
    const invoke = find(node => node.type === "button" && node.props.className === "primary-button")
    assert.equal(invoke.props.disabled, !allowed)
    invoke.props.onClick()
    await nextTurn()
    assert.equal(requests.length, allowed ? 1 : 0)
    if (allowed) {
      assert.equal(requests[0].model_id, "image")
      assert.equal(requests[0].metadata.user_id, "unit@example.com")
      assert.equal(requests[0].n, 1)
      assert.equal(requests[0].stream, false)
      assert.ok(find(node => node.type === "ImageGenerationResult").props.result)
      find(node => node.type === "button" && node.props.children?.includes("文本")).props.onClick()
      find(node => node.type === "button" && node.props.children?.includes("图像")).props.onClick()
      assert.equal(find(node => node.type === "ImageGenerationResult").props.result, null)
    }
  })
}

test("retry client sends paid image authorization only for explicit boolean consent", async () => {
  const start = apiSource.indexOf("  retryGatewayPublication:")
  const end = apiSource.indexOf("  resumeGatewayPublicationAuthorization:", start)
  assert.ok(start >= 0 && end > start)
  const expression = apiSource.slice(start, end).replace(/^\s*retryGatewayPublication:\s*/, "").replace(/,\s*$/, "")
  const writes = []
  const retry = runInNewContext(stripTypeScriptTypes(`(${expression})`), {
    writeJson: async (path, body) => { writes.push({ path, body }); return body },
    normalizeGatewayPublication: value => value,
  })
  for (const consent of [undefined, false, "true", 1]) {
    await retry("publication", undefined, consent)
    assert.equal(writes.at(-1).body.authorize_image_probes, undefined)
  }
  await retry("publication", "unit-key", true)
  assert.equal(writes.at(-1).body.authorize_image_probes, true)
  assert.equal(writes.at(-1).body.api_key, "unit-key")
  assert.equal(writes.at(-1).path, "/api/v1/model-management/publications/publication/retry")
})

test("registry normalization preserves enabled image capability and v4 configuration", () => {
  const defaults = { output_token_reserve: 8192 }
  const registry = normalizeRegistry({
    ...emptyRegistry,
    backend_pool_session_affinity_supported: true,
    image_generation_supported: true,
    image_configuration_defaults: defaults,
    image_configuration_schema_version: 4,
  })
  assert.equal(registry.image_generation_supported, true)
  assert.equal(registry.image_configuration_defaults, defaults)
  assert.equal(registry.image_configuration_schema_version, 4)
  assert.equal(registry.backend_pool_session_affinity_supported, true)
})

test("registry normalization does not enable absent or non-boolean image capability", () => {
  for (const supported of [undefined, null, false, "true", 1]) {
    const registry = normalizeRegistry({
      ...emptyRegistry,
      image_generation_supported: supported,
      image_configuration_schema_version: 4,
    })
    assert.equal(registry.image_generation_supported, false)
  }
})

test("registry normalization preserves unsupported image configuration versions", () => {
  for (const version of [undefined, 3, 5]) {
    const registry = normalizeRegistry({
      ...emptyRegistry,
      image_generation_supported: true,
      image_configuration_defaults: null,
      image_configuration_schema_version: version,
    })
    assert.equal(registry.image_configuration_defaults, null)
    assert.equal(registry.image_configuration_schema_version, version)
    assert.notEqual(registry.image_configuration_schema_version, 4)
  }
})

const result = {
  request_id: "request-image-1",
  output_format: "png",
  data: [{ media_type: "image/png", b64_json: Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).toString("base64") }],
}

test("preview creates a typed Blob without external URLs", async () => {
  const blob = generatedImageBlob(result)
  assert.equal(blob.type, "image/png")
  assert.deepEqual([...new Uint8Array(await blob.arrayBuffer())], [137, 80, 78, 71, 13, 10, 26, 10])
})

for (const data of [[], [...result.data, ...result.data], [{ media_type: "image/svg+xml", b64_json: "abcd" }], [{ media_type: "image/png", b64_json: "!invalid!" }], [{ media_type: "image/png", b64_json: "" }]]) {
  test(`invalid image payload ${JSON.stringify(data)} is rejected`, () => {
    assert.throws(() => generatedImageBlob({ ...result, data }))
  })
}

test("oversized Base64 is rejected before decoding", () => {
  assert.throws(() => generatedImageBlob({ ...result, data: [{ media_type: "image/png", b64_json: "A".repeat(16 * 1024 * 1024 + 1) }] }))
})

test("download names contain no paths or prompt content", () => {
  assert.equal(imageDownloadFilename({ ...result, request_id: "../../unsafe/name" }), "turnstile-unsafename.png")
  assert.equal(imageDownloadFilename({ ...result, request_id: "..." }), "turnstile-image.png")
})

for (const [format, extension] of [["png", "png"], ["jpeg", "jpg"], ["webp", "webp"]]) {
  test(`preview and download preserve returned ${format} format`, () => {
    const response = { ...result, output_format: format, data: [{ ...result.data[0], media_type: `image/${format}` }] }
    assert.equal(generatedImageBlob(response).type, `image/${format}`)
    assert.equal(imageDownloadFilename(response), `turnstile-request-image-1.${extension}`)
  })
}