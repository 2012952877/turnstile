import { createContext, useContext, useState } from "react"

import {
  persistTimezonePreference,
  readTimezonePreference,
  resolvedBrowserTimezone,
  type TimezonePreference,
} from "../lib/timezone"

export type { TimezonePreference } from "../lib/timezone"

type TimezoneContextValue = {
  preference: TimezonePreference
  timezone: string
  browserTimezone: string
  setPreference: (preference: TimezonePreference) => void
}

const TimezoneContext = createContext<TimezoneContextValue | null>(null)

export function TimezoneProvider({ children }: { children: React.ReactNode }) {
  const [preference, setPreferenceState] = useState<TimezonePreference>(readTimezonePreference)
  const browserTimezone = resolvedBrowserTimezone()
  const timezone = preference === "UTC" ? "UTC" : browserTimezone

  const setPreference = (next: TimezonePreference) => {
    persistTimezonePreference(next)
    setPreferenceState(next)
  }

  return (
    <TimezoneContext.Provider value={{ preference, timezone, browserTimezone, setPreference }}>
      {children}
    </TimezoneContext.Provider>
  )
}

export function useTimezone() {
  const context = useContext(TimezoneContext)
  if (!context) throw new Error("useTimezone must be used within TimezoneProvider")
  return context
}