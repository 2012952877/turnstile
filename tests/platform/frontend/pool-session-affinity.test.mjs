import assert from "node:assert/strict"
import test from "node:test"

import { poolPublicationPayload } from "../../../frontend/src/components/model-management/pool-session-affinity.ts"

const draft = {
  members: [
    { runtime_id: "one", priority: 0, weight: 1 },
    { runtime_id: "two", priority: 0, weight: 1 },
  ],
  rate_limit: { max_attempts_per_request: 2 },
}

test("legacy backend payload omits a false or missing affinity field", () => {
  assert.deepEqual(poolPublicationPayload(draft, false), draft)
  assert.deepEqual(poolPublicationPayload({ ...draft, session_affinity: false }, false), draft)
})

test("unsupported affinity cannot silently downgrade to ordinary balancing", () => {
  assert.throws(() => poolPublicationPayload({ ...draft, session_affinity: true }, false))
})

test("capable backends receive an explicit boolean without altering member weights", () => {
  for (const enabled of [false, true]) {
    assert.deepEqual(poolPublicationPayload({ ...draft, session_affinity: enabled }, true),
      { ...draft, session_affinity: enabled })
  }
  assert.deepEqual(poolPublicationPayload(draft, true), { ...draft, session_affinity: false })
})

test("payload conversion does not mutate the in-progress draft", () => {
  const value = { ...draft, session_affinity: false }
  const before = structuredClone(value)
  const result = poolPublicationPayload(value, false)
  assert.notEqual(result, value)
  assert.deepEqual(value, before)
})