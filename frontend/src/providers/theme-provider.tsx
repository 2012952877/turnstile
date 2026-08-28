import { createContext, useContext, useEffect, useState } from "react"

export type ThemePreference = "light" | "dark" | "system"

type ThemeContextValue = {
  preference: ThemePreference
  resolvedTheme: "light" | "dark"
  setPreference: (preference: ThemePreference) => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)
const COOKIE_NAME = "finops_theme"

function readPreference(): ThemePreference {
  const value = document.cookie
    .split("; ")
    .find((entry) => entry.startsWith(`${COOKIE_NAME}=`))
    ?.split("=")[1]
  return value === "light" || value === "dark" || value === "system" ? value : "system"
}

function systemTheme(): "light" | "dark" {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(readPreference)
  const [resolvedTheme, setResolvedTheme] = useState<"light" | "dark">(() =>
    preference === "system" ? systemTheme() : preference,
  )

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)")
    const apply = () => {
      const resolved = preference === "system" ? (media.matches ? "dark" : "light") : preference
      document.documentElement.classList.toggle("dark", resolved === "dark")
      document.documentElement.dataset.themePreference = preference
      document.documentElement.style.colorScheme = resolved
      setResolvedTheme(resolved)
    }
    apply()
    media.addEventListener("change", apply)
    return () => media.removeEventListener("change", apply)
  }, [preference])

  const setPreference = (next: ThemePreference) => {
    document.cookie = `${COOKIE_NAME}=${next}; Max-Age=31536000; Path=/; SameSite=Lax`
    setPreferenceState(next)
  }

  return (
    <ThemeContext.Provider value={{ preference, resolvedTheme, setPreference }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  const context = useContext(ThemeContext)
  if (!context) throw new Error("useTheme must be used within ThemeProvider")
  return context
}
