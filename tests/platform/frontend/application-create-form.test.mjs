import assert from "node:assert/strict"
import test from "node:test"

import {
  applicationCreateError,
  applicationOperationIdFromUrl,
  applicationOperationTerminal,
  applicationProvisionBlocker,
  applicationProvisionStage,
  applicationSubscriptionIdFromName,
  provisionedApplicationId,
} from "../../../frontend/src/components/applications/application-create-form.ts"

const gateway = { id: "gateway", implementation: "apim", enabled: true }
const request = { display_name: "Unit application", subscription_id: "unit-app" }
const applicationId = "00000000-0000-4000-8000-000000000001"

test("subscription ID normalization is locale independent and bounded", () => {
  assert.equal(applicationSubscriptionIdFromName(" Café / INVOICE "), "cafe-invoice")
  assert.equal(applicationSubscriptionIdFromName("中文"), "")
  assert.equal(applicationSubscriptionIdFromName("x".repeat(126) + "--test"), "x".repeat(126))
  assert.equal(applicationSubscriptionIdFromName("x".repeat(150)).length, 127)
})

test("creation requires an enabled APIM gateway and valid unreserved identity", () => {
  assert.equal(applicationCreateError(gateway, request, []), null)
  for (const candidate of [undefined, { ...gateway, enabled: false }, { ...gateway, implementation: "direct" }]) {
    assert.ok(applicationCreateError(candidate, request, []))
  }
  for (const subscription_id of ["UPPER", "bad_id", "x".repeat(128), "master", "turnstile-dashboard", "turnstile-publisher-probe"]) {
    assert.ok(applicationCreateError(gateway, { ...request, subscription_id }, []))
  }
  assert.ok(applicationCreateError(gateway, { ...request, display_name: " " }, []))
  assert.ok(applicationCreateError(gateway, { ...request, description: "x".repeat(1001) }, []))
})

test("duplicate inventory identity is scoped to the selected gateway", () => {
  const duplicate = { gateway_profile_id: "gateway", slug: "UNIT-APP" }
  assert.ok(applicationCreateError(gateway, request, [duplicate]))
  assert.equal(applicationCreateError({ ...gateway, id: "other" }, request, [duplicate]), null)
})

test("capability remains unavailable until the server explicitly enables it", () => {
  assert.ok(applicationProvisionBlocker(undefined))
  assert.ok(applicationProvisionBlocker({ provisioning_available: false }))
  assert.equal(applicationProvisionBlocker({ provisioning_available: true }), null)
})

test("only successful creation yields an application detail destination", () => {
  const operation = { operation_kind: "application_provision", status: "succeeded", checkpoint: { application_id: applicationId } }
  assert.equal(provisionedApplicationId(operation), applicationId)
  for (const status of ["queued", "promoting", "verifying_readback", "failed"]) {
    assert.equal(provisionedApplicationId({ ...operation, status }), null)
  }
  assert.equal(provisionedApplicationId({ ...operation, operation_kind: "application_sync" }), null)
  assert.equal(provisionedApplicationId({ ...operation, checkpoint: { application_id: "bad" } }), null)
})

test("operation URLs never carry keys and accept UUIDs only", () => {
  assert.equal(applicationOperationIdFromUrl(`https://unit.test/?applicationOperation=${applicationId}`), applicationId)
  assert.equal(applicationOperationIdFromUrl("https://unit.test/?applicationOperation=invalid"), null)
  assert.equal(applicationOperationIdFromUrl("https://unit.test/"), null)
})

test("staged progress distinguishes disabled, terminal and ledger verification", () => {
  assert.equal(applicationOperationTerminal({ status: "verifying_readback" }), false)
  assert.equal(applicationOperationTerminal({ status: "failed" }), true)
  assert.notEqual(applicationProvisionStage({ status: "queued", worker_available: false }), applicationProvisionStage({ status: "queued" }))
  assert.notEqual(applicationProvisionStage({ status: "promoting" }), applicationProvisionStage({ status: "verifying_readback" }))
  assert.notEqual(applicationProvisionStage({ status: "succeeded" }), applicationProvisionStage({ status: "failed" }))
})