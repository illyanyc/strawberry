import { CandlestickChart, CornerDownLeft, LogOut, Moon, OctagonX, Search, Sun } from "lucide-react";
import { type ReactNode, useEffect, useId, useMemo, useRef, useState } from "react";

import { Dialog } from "@/components/ui/dialog";
import { Kbd } from "@/components/ui/layout";
import { matchNav, NAV_SECTIONS } from "@/lib/nav";
import { cn } from "@/lib/utils";

export interface PaletteActions {
  go: (to: string, search?: Record<string, string>) => void;
  toggleTheme: () => void;
  theme: "dark" | "light";
  openKillSwitch: () => void;
  logout: () => void;
}

interface Entry {
  id: string;
  label: string;
  group: string;
  icon: ReactNode;
  hint?: string;
  run: () => void;
  danger?: boolean;
}

const SYMBOL_RE = /^[A-Za-z][A-Za-z0-9.]{0,9}$/;

/** Builds the palette's entries for a query: pages, a chart shortcut for ticker-like input, actions. */
export function paletteEntries(query: string, a: PaletteActions): Entry[] {
  const q = query.trim();
  const out: Entry[] = [];
  if (SYMBOL_RE.test(q)) {
    const sym = q.toUpperCase();
    out.push({ id: `chart:${sym}`, label: `Open ${sym} chart`, group: "Go to", icon: <CandlestickChart />, run: () => a.go("/charts", { symbol: sym }) });
  }
  const section = (id: string) => NAV_SECTIONS.find((s) => s.id === id)?.label ?? id;
  for (const n of matchNav(q)) {
    const Icon = n.icon;
    out.push({ id: `nav:${n.to}`, label: n.label, group: section(n.section), icon: <Icon />, hint: n.key ? `g ${n.key}` : undefined, run: () => a.go(n.to) });
  }
  const actions: Entry[] = [
    { id: "act:theme", label: a.theme === "dark" ? "Switch to light theme" : "Switch to dark theme", group: "Actions", icon: a.theme === "dark" ? <Sun /> : <Moon />, hint: "⇧T", run: a.toggleTheme },
    { id: "act:kill", label: "Engage global kill switch…", group: "Actions", icon: <OctagonX />, run: a.openKillSwitch, danger: true },
    { id: "act:logout", label: "Log out", group: "Actions", icon: <LogOut />, run: a.logout },
  ];
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  out.push(...actions.filter((e) => words.every((w) => `${e.label} theme kill switch halt freeze logout`.toLowerCase().includes(w))));
  return out;
}

/**
 * ⌘K command palette (UI-042): pages, symbols, and actions. Keyboard: arrows to move, Enter to run,
 * Esc to close. Uses the WAI-ARIA combobox + listbox pattern.
 */
export function CommandPalette({ open, onClose, actions }: { open: boolean; onClose: () => void; actions: PaletteActions }) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listId = useId();
  const input = useRef<HTMLInputElement>(null);
  const entries = useMemo(() => paletteEntries(query, actions), [query, actions]);

  useEffect(() => {
    if (!open) return;
    // The dialog focuses its first focusable element; make sure it is the search box.
    const t = setTimeout(() => input.current?.focus(), 0);
    return () => clearTimeout(t);
  }, [open]);

  // Reset on close so the next open starts empty.
  const close = () => {
    setQuery("");
    setActive(0);
    onClose();
  };
  const run = (e: Entry | undefined) => {
    if (!e) return;
    close();
    e.run();
  };

  return (
    <Dialog open={open} onClose={close} title="Command palette" hideTitle size="md" placement="top">
      <div className="flex items-center gap-2 border-b border-border px-4">
        <Search className="size-4 text-muted" aria-hidden />
        <input
          ref={input}
          role="combobox"
          aria-expanded="true"
          aria-controls={listId}
          aria-activedescendant={entries[active] ? `${listId}-${active}` : undefined}
          aria-label="Search pages, symbols, and actions"
          placeholder="Search pages, symbols, actions…"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActive((i) => Math.min(i + 1, entries.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((i) => Math.max(i - 1, 0));
            } else if (e.key === "Enter") {
              e.preventDefault();
              run(entries[active]);
            }
          }}
          className="h-12 w-full bg-transparent text-sm outline-none placeholder:text-subtle"
        />
        <Kbd>Esc</Kbd>
      </div>
      <ul id={listId} role="listbox" aria-label="Results" className="max-h-[min(60vh,28rem)] overflow-y-auto p-2">
        {entries.length === 0 && <li className="px-3 py-6 text-center text-sm text-muted">No matches.</li>}
        {entries.map((e, i) => {
          const header = i === 0 || entries[i - 1]!.group !== e.group ? e.group : null;
          return (
            <li key={e.id} role="presentation">
              {header && <p className="px-3 pb-1 pt-2 text-[10.5px] font-semibold uppercase tracking-wider text-subtle">{header}</p>}
              <div
                id={`${listId}-${i}`}
                role="option"
                aria-selected={i === active}
                onMouseMove={() => setActive(i)}
                onClick={() => run(e)}
                className={cn(
                  "flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2 text-sm [&_svg]:size-4",
                  i === active ? "bg-panel-3 text-fg" : "text-muted",
                  e.danger && "text-live",
                )}
              >
                <span aria-hidden>{e.icon}</span>
                <span className="flex-1 truncate">{e.label}</span>
                {e.hint && <span className="font-mono text-[11px] text-subtle">{e.hint}</span>}
                {i === active && <CornerDownLeft className="text-subtle" aria-hidden />}
              </div>
            </li>
          );
        })}
      </ul>
    </Dialog>
  );
}

/** Shortcut reference opened with `?`. */
export function ShortcutHelp({ open, onClose, items }: { open: boolean; onClose: () => void; items: { keys: string; label: string }[] }) {
  return (
    <Dialog open={open} onClose={onClose} title="Keyboard shortcuts" size="md">
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
        {items.map((i) => (
          <div key={i.keys} className="contents">
            <dt className="flex gap-1">
              {i.keys.split(" ").map((k) => (
                <Kbd key={k}>{k}</Kbd>
              ))}
            </dt>
            <dd className="text-muted">{i.label}</dd>
          </div>
        ))}
      </dl>
    </Dialog>
  );
}
