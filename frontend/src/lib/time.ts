import type { LocalePreference } from "../locales/provider"

/** Format one server timestamp directly in the reader's locale.
 *
 * Relative time is dynamic data, not source-language UI copy. Producing Chinese here and
 * asking the DOM phrase converter to parse it made generic rules compete with specific
 * ones (`1 天` consumed the front of `1 天前`). Intl owns grammar and pluralization in one
 * path; callers mark the resulting text as data-no-localize so it is never translated a
 * second time.
 */
export function formatTimeAgo(
  iso: string,
  locale: LocalePreference,
  now = Date.now(),
): string {
  const elapsedMs = Math.max(0, now - new Date(iso).getTime())
  const minutes = Math.floor(elapsedMs / 60_000)
  if (minutes < 1) {
    return new Intl.RelativeTimeFormat(locale, { numeric: "auto" }).format(0, "second")
  }

  const relative = new Intl.RelativeTimeFormat(locale, { numeric: "always" })
  if (minutes < 60) return relative.format(-minutes, "minute")

  const hours = Math.floor(minutes / 60)
  if (hours < 24) return relative.format(-hours, "hour")

  const days = Math.floor(hours / 24)
  if (days < 7) return relative.format(-days, "day")

  return new Intl.DateTimeFormat(locale).format(new Date(iso))
}
