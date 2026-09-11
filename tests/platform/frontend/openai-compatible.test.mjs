import assert from "node:assert/strict"
import test from "node:test"

import {
  displayNameFromProviderId,
  isOpenAICompatibleBaseUrl,
  isUnsupportedOpenAICompatibleDetails,
  modelVendorFromMetadata,
  modelVendorLabel,
  publicModelKeyFromProviderId,
} from "../../../frontend/src/components/model-management/openai-compatible.ts"

test("compatible endpoints accept HTTPS bases and full chat paths only without URL credentials", () => {
  for (const value of ["https://api.example.test/v1", " https://api.example.test/v1/chat/completions "]) {
    assert.equal(isOpenAICompatibleBaseUrl(value), true)
  }
  for (const value of ["", "/v1", "http://api.example.test", "https://user:value@api.example.test",
    "https://api.example.test/v1?key=sample", "https://api.example.test/#fragment"]) {
    assert.equal(isOpenAICompatibleBaseUrl(value), false)
  }
})

test("public aliases are normalized without changing the provider model ID", () => {
  const providerId = " Team/Model_V2 "
  assert.equal(publicModelKeyFromProviderId(providerId), "team-model_v2")
  assert.equal(providerId, " Team/Model_V2 ")
  assert.equal(publicModelKeyFromProviderId("///"), "")
  assert.equal(publicModelKeyFromProviderId("gpt-4.1:mini"), "gpt-4.1:mini")
})

test("display names use the model leaf while preserving recognizable vendor names", () => {
  assert.equal(displayNameFromProviderId("team/deepseek-v3.2"), "DeepSeek V3.2")
  assert.equal(displayNameFromProviderId("gpt-4.1"), "GPT 4.1")
  assert.equal(displayNameFromProviderId("team/kimi-k2"), "Kimi K2")
  assert.equal(displayNameFromProviderId(""), "")
})

test("explicit vendor metadata wins over a display-name guess", () => {
  assert.equal(modelVendorFromMetadata({ model_vendor: "generic" }, "OpenAI compatible GPT"), "generic")
  assert.equal(modelVendorFromMetadata({ model_vendor: "kimi" }, "DeepSeek"), "kimi")
  assert.equal(modelVendorFromMetadata(undefined, "OpenAI-compatible"), "generic")
  assert.equal(modelVendorFromMetadata(undefined, "moonshot.example.test"), "kimi")
  assert.equal(modelVendorFromMetadata(undefined, "DeepSeek connection"), "deepseek")
  assert.equal(modelVendorLabel("anthropic"), "Anthropic")
})

test("old schema detection requires both exact provider and endpoint rejections", () => {
  const provider = { type: "literal_error", loc: ["body", "provider", "template"], input: "openai_compatible" }
  const endpoint = { type: "extra_forbidden", loc: ["body", "openai_base_url"] }
  assert.equal(isUnsupportedOpenAICompatibleDetails({ detail: [provider, endpoint] }), true)
  for (const value of [null, {}, { detail: "invalid" }, { detail: [provider] }, { detail: [endpoint] },
    { detail: [{ ...provider, input: "microsoft_foundry" }, endpoint] },
    { detail: [{ ...provider, loc: ["body", "other", "template"] }, endpoint] },
    { detail: [null, 1, "value"] }]) {
    assert.equal(isUnsupportedOpenAICompatibleDetails(value), false)
  }
})