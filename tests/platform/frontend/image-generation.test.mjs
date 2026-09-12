import assert from "node:assert/strict"
import test from "node:test"

import { generatedImageBlob, imageDownloadFilename } from "../../../frontend/src/data-sources/apim/image-generation.ts"

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