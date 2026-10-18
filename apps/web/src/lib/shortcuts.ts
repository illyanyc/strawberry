import { useEffect, useRef } from "react";

import { navByKey } from "./nav";

/**
 * Desktop keyboard shortcuts (UI-042):
 *   ⌘K / Ctrl+K  command palette        ?  shortcut help
 *   g <key>      go to a page (keys in lib/nav.ts)
 *   Shift+T      toggle the color theme
 * Plain keys are ignored while typing in a field, and no shortcut performs a trading action: the
 * palette's kill-switch entry only opens the usual confirmation dialog.
 */
export interface ShortcutHandlers {
  palette: () => void;
  help: () => void;
  go: (to: string) => void;
  toggleTheme: () => void;
}

export const SEQUENCE_TIMEOUT_MS = 1200;

export function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  if (t.isContentEditable) return true;
  const tag = t.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag !== "INPUT") return false;
  const type = (t as HTMLInputElement).type;
  return !["checkbox", "radio", "button", "submit", "range", "color"].includes(type);
}

/** Pure key handling, separated for tests. Returns true when the event was consumed. */
export function handleKey(e: Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "altKey" | "shiftKey" | "target">, h: ShortcutHandlers, state: { pendingG: number }, now: number): boolean {
  if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "k") {
    h.palette();
    return true;
  }
  if (e.metaKey || e.ctrlKey || e.altKey || isTypingTarget(e.target)) return false;
  if (state.pendingG && now - state.pendingG <= SEQUENCE_TIMEOUT_MS) {
    state.pendingG = 0;
    const item = navByKey(e.key.toLowerCase());
    if (item) {
      h.go(item.to);
      return true;
    }
    return false;
  }
  state.pendingG = 0;
  if (e.key === "g") {
    state.pendingG = now;
    return true;
  }
  if (e.key === "?") {
    h.help();
    return true;
  }
  if (e.key === "T" && e.shiftKey) {
    h.toggleTheme();
    return true;
  }
  return false;
}

export function useShortcuts(h: ShortcutHandlers): void {
  const ref = useRef(h);
  useEffect(() => {
    ref.current = h;
  }, [h]);
  useEffect(() => {
    const state = { pendingG: 0 };
    const onKey = (e: KeyboardEvent) => {
      // Never steal keys from an open modal other than the palette toggle.
      const inDialog = e.target instanceof HTMLElement && e.target.closest("dialog[open]") !== null;
      if (inDialog && !((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k")) return;
      if (handleKey(e, ref.current, state, Date.now())) e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
