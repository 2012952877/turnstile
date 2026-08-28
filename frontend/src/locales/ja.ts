import { JAPANESE_CORE_PHRASES } from "./ja/phrases-core"
import { JAPANESE_COPILOT_PHRASES } from "./ja/phrases-copilot"
import { JAPANESE_AUTH_PHRASES } from "./ja/phrases-auth"
import { DYNAMIC_RULES } from "./ja/dynamic-rules"

const JAPANESE_PHRASES: Record<string, string> = {
  ...JAPANESE_CORE_PHRASES,
  ...JAPANESE_COPILOT_PHRASES,
  ...JAPANESE_AUTH_PHRASES,
}

const orderedPhrases = Object.entries(JAPANESE_PHRASES).sort(([left], [right]) => right.length - left.length)

export function translateToJapanese(input: string): string {
  const whitespace = input.match(/^(\s*)([\s\S]*?)(\s*)$/)
  if (!whitespace) return input
  const [, before, content, after] = whitespace
  const exact = JAPANESE_PHRASES[content]
  if (exact != null) return `${before}${exact}${after}`

  let translated = content
  for (const [pattern, replacement] of DYNAMIC_RULES) translated = translated.replace(pattern, replacement)
  for (const [source, target] of orderedPhrases) translated = translated.replaceAll(source, target)

  translated = translated
    .replaceAll("，", "、")
    .replaceAll("。", "。")
    .replaceAll("：", "：")
    .replaceAll("；", "；")
    .replaceAll("（", "（")
    .replaceAll("）", "）")

  return `${before}${translated}${after}`
}
