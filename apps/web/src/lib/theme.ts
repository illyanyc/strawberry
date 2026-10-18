import { useCallback, useSyncExternalStore } from "react";

/**
 * Color theme (UI-040): dark by default, light on request. The choice is a display preference
 * stored in localStorage (never a session or secret, UI-I1) and applied as a class on <html>.
 */
export type Theme = "dark" | "light";

const KEY = "tb.theme";
const listeners = new Set<() => void>();

function read(): Theme {
  try {
    return localStorage.getItem(KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

/** Apply the stored theme to <html>; called once at startup and on every change. */
export function applyTheme(theme: Theme = read()): void {
  const root = document.documentElement;
  root.classList.toggle("light", theme === "light");
  root.classList.toggle("dark", theme === "dark");
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "light" ? "#f5f7fa" : "#090c11");
}

export function setTheme(theme: Theme): void {
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // Private mode: the theme still applies for this page.
  }
  applyTheme(theme);
  for (const l of listeners) l();
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

/** Current theme and a toggle; components that draw with CSS variables (charts) re-read on change. */
export function useTheme(): [Theme, () => void] {
  const theme = useSyncExternalStore(subscribe, read, () => "dark" as Theme);
  const toggle = useCallback(() => setTheme(read() === "dark" ? "light" : "dark"), []);
  return [theme, toggle];
}
