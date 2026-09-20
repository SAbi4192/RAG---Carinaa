import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

/**
 * Theme state.
 *
 * The stored value is one of "light" | "dark" | "system" - the user's CHOICE.
 * The applied value is always "light" or "dark" - the RESOLUTION of that choice.
 * Keeping them separate is what lets "follow my system" be a real option rather
 * than a third colour scheme, and it is why changing the OS theme while the app
 * is open updates the UI live.
 *
 * The key and the resolution logic must stay in sync with the inline bootstrap
 * script in `index.html`, which applies the theme before React mounts. If they
 * disagree you get a flash of the wrong theme on every load.
 */

export const THEME_KEY = "carinaa.theme";

export type ThemeChoice = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

interface ThemeContextValue {
  /** What the user picked. */
  theme: ThemeChoice;
  /** What is actually on screen right now. */
  resolved: ResolvedTheme;
  setTheme: (theme: ThemeChoice) => void;
  /** Flip between light and dark, leaving "system" behind. */
  toggle: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function readStoredTheme(): ThemeChoice {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === "light" || stored === "dark" || stored === "system") return stored;
  } catch {
    /* storage unavailable */
  }
  return "system";
}

function systemTheme(): ResolvedTheme {
  if (typeof window === "undefined" || !window.matchMedia) return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeChoice>(readStoredTheme);
  const [systemResolved, setSystemResolved] = useState<ResolvedTheme>(systemTheme);

  // Track the OS preference. Registered even when the choice is not "system",
  // because the user may switch to "system" later and should then immediately
  // inherit the current OS value rather than a stale one.
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (event: MediaQueryListEvent) => {
      setSystemResolved(event.matches ? "dark" : "light");
    };
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  const resolved: ResolvedTheme = theme === "system" ? systemResolved : theme;

  // Apply to <html>. `color-scheme` matters as much as `data-theme`: it tells the
  // browser to render form controls, scrollbars and the canvas in the matching
  // scheme, which is the difference between a real dark mode and a dark page
  // with white dropdowns.
  useEffect(() => {
    const root = document.documentElement;
    root.setAttribute("data-theme", resolved);
    root.style.colorScheme = resolved;
  }, [resolved]);

  const setTheme = useCallback((next: ThemeChoice) => {
    setThemeState(next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      /* non-fatal: the theme just will not persist across reloads */
    }
  }, []);

  const toggle = useCallback(() => {
    setTheme(resolved === "dark" ? "light" : "dark");
  }, [resolved, setTheme]);

  const value = useMemo(
    () => ({ theme, resolved, setTheme, toggle }),
    [theme, resolved, setTheme, toggle],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useTheme must be used inside a ThemeProvider.");
  return context;
}
