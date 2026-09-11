import assert from "node:assert/strict"
import test from "node:test"

import { DEFAULT_MIN_COLUMN_WIDTH, preserveColumnWidths } from "../../../frontend/src/components/ui/column-widths.ts"

const minimums = [180, 120, 160, 80, 48]

test("repeated narrow and wide viewports preserve hidden column widths", () => {
  const desktop = preserveColumnWidths([323.2, 272, 314.5, 100, 106.7], null, minimums)
  let remembered = desktop
  for (let iteration = 0; iteration < 5; iteration += 1) {
    remembered = preserveColumnWidths([191.3, 0, 0, 91.3, 59.3], remembered, minimums)
    assert.deepEqual(remembered, [191, 272, 315, 91, 59])
    remembered = preserveColumnWidths([215, 0, 0, 91, 59], remembered, minimums)
    assert.deepEqual(remembered.slice(1, 3), desktop.slice(1, 3))
  }
})

test("first mobile render falls back to each unseen column minimum", () => {
  assert.deepEqual(preserveColumnWidths([180, 0, 0, 90, 72], null, minimums), [180, 120, 160, 90, 72])
})

test("valid measurements replace history without clamping unrelated columns", () => {
  assert.deepEqual(preserveColumnWidths([200.4, 120.6, 160.5, 79.8, 40.4], [400, 300, 300, 100, 160], minimums), [200, 121, 161, 80, 40])
})

test("hiding an entire table retains its last visible widths", () => {
  const previous = [323, 252, 315, 100, 107]
  assert.deepEqual(preserveColumnWidths([0, 0, 0, 0, 0], previous, minimums), previous)
})

for (const observer of [null, [323, 252, 315, 100, 107]]) {
  test(`committed widths take priority over ${observer ? "stale" : "pending"} observer history`, () => {
    const widths = [323, 272, 315, 100, 107]
    assert.deepEqual(preserveColumnWidths([191, 0, 0, 91, 59], widths ?? observer, minimums), [191, 272, 315, 91, 59])
  })
}

for (const invalid of [0, -1, Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY]) {
  test(`invalid width ${invalid} cannot erase history or produce zero`, () => {
    assert.deepEqual(preserveColumnWidths([invalid], [123], [80]), [123])
    assert.deepEqual(preserveColumnWidths([0], [invalid], [80]), [80])
    assert.deepEqual(preserveColumnWidths([0], null, [invalid]), [DEFAULT_MIN_COLUMN_WIDTH])
  })
}

test("missing values and subpixel measurements have finite positive fallbacks", () => {
  assert.deepEqual(preserveColumnWidths([0, 0, 0], [140], [80, 90]), [140, 90, 72])
  assert.deepEqual(preserveColumnWidths([], [140], minimums), [])
  assert.deepEqual(preserveColumnWidths([0.1, 0], [0, 0.1]), [1, 1])
})

test("measurement and history arrays are never mutated", () => {
  const measured = Object.freeze([190, 0, 0, 90, 72])
  const previous = Object.freeze([323, 252, 315, 100, 107])
  const result = preserveColumnWidths(measured, previous, Object.freeze(minimums))
  assert.deepEqual(result, [190, 252, 315, 90, 72])
  assert.notEqual(result, previous)
  assert.deepEqual(previous, [323, 252, 315, 100, 107])
})