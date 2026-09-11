import assert from "node:assert/strict"
import test from "node:test"

import {
  createModelEditDraft,
  modelEditHasChanges,
  modelEditPayload,
  modelEditRoleOptions,
  setModelEditDefault,
  setModelEditEnabled,
  toggleModelRole,
  validateModelEdit,
} from "../../../frontend/src/components/model-management/model-edit-form.ts"

function savedModel(overrides = {}) {
  return {
    id: "model-id",
    provider_id: "provider-id",
    runtime_id: "runtime-id",
    model_key: "published-model",
    upstream_model_id: "upstream-deployment",
    family_key: "generic",
    assignment_required: true,
    capabilities: ["chat", "streaming"],
    display_name: "Existing model",
    context_window: 128000,
    input_cost_per_million: 2,
    output_cost_per_million: 8,
    cached_cost_per_million: 0.5,
    cache_write_cost_per_million: 1,
    allowed_roles: ["owner", "member", "custom-role"],
    enabled: true,
    is_default: false,
    ...overrides,
  }
}

test("unchanged model metadata round-trips without a pending change", () => {
  const model = savedModel()
  const draft = createModelEditDraft(model)
  const payload = modelEditPayload(model, draft)
  assert.equal(modelEditHasChanges(draft, createModelEditDraft(model)), false)
  for (const [key, value] of Object.entries(payload)) assert.deepEqual(value, model[key], key)
  assert.equal("id" in payload, false)
  assert.equal("gateway_profile_id" in payload, false)
})

test("draft fields cannot replace saved routing identity or capabilities", () => {
  const model = savedModel()
  const draft = {
    ...createModelEditDraft(model),
    displayName: " Renamed model ",
    inputPrice: "3.123456789",
    provider_id: "other-provider",
    runtime_id: "other-runtime",
    model_key: "other-alias",
    upstream_model_id: "other-deployment",
    family_key: "other-family",
    assignment_required: false,
    capabilities: ["vision"],
  }
  const payload = modelEditPayload(model, draft)
  for (const key of ["provider_id", "runtime_id", "model_key", "upstream_model_id",
    "family_key", "assignment_required", "capabilities"]) {
    assert.deepEqual(payload[key], model[key], key)
  }
  assert.equal(payload.display_name, "Renamed model")
  assert.equal(payload.input_cost_per_million, 3.123456789)
  assert.notEqual(payload.capabilities, model.capabilities)
})

test("nulls and explicit zero prices retain distinct meanings", () => {
  const model = savedModel({
    upstream_model_id: null,
    context_window: null,
    input_cost_per_million: null,
    output_cost_per_million: 0,
    cached_cost_per_million: null,
    cache_write_cost_per_million: 0,
  })
  const draft = createModelEditDraft(model)
  assert.equal(draft.contextWindow, "")
  assert.equal(draft.inputPrice, "")
  assert.equal(draft.outputPrice, "0")
  const payload = modelEditPayload(model, draft)
  for (const [key, value] of Object.entries(payload)) assert.deepEqual(value, model[key], key)
})

test("cleared cache rates request backend fallback without inventing prices", () => {
  const model = savedModel()
  const payload = modelEditPayload(model, {
    ...createModelEditDraft(model), cacheReadPrice: "", cacheWritePrice: " ",
  })
  assert.equal(payload.cached_cost_per_million, null)
  assert.equal(payload.cache_write_cost_per_million, null)
  assert.equal(payload.input_cost_per_million, model.input_cost_per_million)
})

test("role options preserve custom roles without granting them implicitly", () => {
  const model = savedModel({ allowed_roles: ["admin", "custom-role"] })
  const draft = createModelEditDraft(model)
  assert.deepEqual(modelEditRoleOptions(model), ["owner", "member", "admin", "custom-role"])
  assert.deepEqual(modelEditPayload(model, draft).allowed_roles, model.allowed_roles)
  assert.notEqual(draft.allowedRoles, model.allowed_roles)
  const roles = toggleModelRole(draft.allowedRoles, "owner", true)
  assert.deepEqual(toggleModelRole(roles, "owner", true), roles)
  assert.deepEqual(toggleModelRole(roles, "owner", false), model.allowed_roles)
  assert.deepEqual(modelEditPayload(model, { ...draft, allowedRoles: [] }).allowed_roles, [])
})

test("restored roles are unchanged regardless of checkbox order", () => {
  const initial = createModelEditDraft(savedModel())
  assert.equal(modelEditHasChanges(initial, {
    ...initial, allowedRoles: [...initial.allowedRoles].reverse(),
  }), false)
  assert.equal(modelEditHasChanges(initial, { ...initial, allowedRoles: [] }), true)
})

test("invalid names cannot produce a save payload", () => {
  const model = savedModel()
  for (const displayName of ["", "   ", "x".repeat(256)]) {
    const draft = { ...createModelEditDraft(model), displayName }
    assert.equal(validateModelEdit(draft), "display_name")
    assert.throws(() => modelEditPayload(model, draft), /display_name/)
  }
})

test("context windows require positive safe integers and prices finite non-negative values", () => {
  const draft = createModelEditDraft(savedModel())
  for (const contextWindow of ["0", "-1", "1.5", "Infinity", "invalid", "9007199254740992"]) {
    assert.equal(validateModelEdit({ ...draft, contextWindow }), "context_window")
  }
  for (const price of ["-1", "Infinity", "NaN", "1e999"]) {
    for (const field of ["inputPrice", "outputPrice", "cacheReadPrice", "cacheWritePrice"]) {
      assert.equal(validateModelEdit({ ...draft, [field]: price }), "prices")
    }
  }
  assert.equal(validateModelEdit({ ...draft, contextWindow: "1", inputPrice: "0" }), null)
})

test("default and enabled controls cannot produce a disabled default", () => {
  const model = savedModel()
  const initial = createModelEditDraft(model)
  const disabled = setModelEditEnabled(initial, false)
  const promoted = setModelEditDefault(disabled, true)
  assert.equal(promoted.enabled, true)
  assert.equal(promoted.isDefault, true)
  assert.equal(setModelEditEnabled(promoted, false).isDefault, false)
  assert.equal(setModelEditDefault(promoted, false).enabled, true)
  const invalid = { ...initial, enabled: false, isDefault: true }
  assert.equal(validateModelEdit(invalid), "default_disabled")
  assert.throws(() => modelEditPayload(model, invalid), /default_disabled/)
  assert.equal(initial.enabled, true)
  assert.equal(initial.isDefault, false)
})