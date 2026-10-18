import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import { Keyboard, LogOut, Menu, Moon, PanelLeftClose, PanelLeftOpen, Search, Sun } from "lucide-react";
import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";

import { CommandPalette, ShortcutHelp, type PaletteActions } from "@/components/CommandPalette";
import { EnvironmentBanner } from "@/components/EnvironmentBanner";
import { EngageKillDialog, GlobalKillSwitch } from "@/components/KillSwitch";
import { BrandMark } from "@/components/BrandMark";
import { StatusStrip } from "@/components/StatusStrip";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Kbd } from "@/components/ui/layout";
import { api, type TradingStatus } from "@/lib/api";
import { STALE_AFTER_MS, useNow, useTradingStatus } from "@/lib/events";
import { mobilePrimary, NAV, NAV_SECTIONS, navSection, type NavItem } from "@/lib/nav";
import { useShortcuts } from "@/lib/shortcuts";
import { useTheme } from "@/lib/theme";
import { cn, formatTime } from "@/lib/utils";

const SIDEBAR_KEY = "tb.sidebar";

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === "collapsed";
  } catch {
    return false;
  }
}

const SHORTCUTS = [
  { keys: "⌘ K", label: "Command palette (Ctrl K on Windows/Linux)" },
  { keys: "?", label: "This help" },
  { keys: "⇧ T", label: "Toggle light/dark theme" },
  ...NAV.filter((n) => n.key).map((n) => ({ keys: `g ${n.key}`, label: `Go to ${n.label}` })),
];

/**
 * Authenticated layout (UI-040, UI-041): environment banner, trading status strip, sidebar on
 * desktop and a tab bar plus "More" sheet on phones, the global kill switch in the header on every
 * screen, the command palette, and keyboard shortcuts.
 */
export function AppShell() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const onSessionEnded = useCallback(() => void navigate({ to: "/login" }), [navigate]);
  const stream = useTradingStatus(onSessionEnded);
  const { connected, lastEventAt } = stream;
  // The REST snapshot is refetched right after a settings change; the SSE stream updates periodically.
  // Show whichever the server produced most recently so the banner never lags a change.
  const snapshot = useQuery({ queryKey: ["status"], queryFn: () => api<TradingStatus>("/api/v1/system/status") });
  const status = latest(stream.status, snapshot.data ?? null);
  const now = useNow();
  const stale = lastEventAt === null || now - lastEventAt > STALE_AFTER_MS;
  const [theme, toggleTheme] = useTheme();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [palette, setPalette] = useState(false);
  const [help, setHelp] = useState(false);
  const [more, setMore] = useState(false);
  const [killOpen, setKillOpen] = useState(false);

  const logout = useCallback(async () => {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    // Never show one session's data to the next one (UI-009).
    queryClient.clear();
    void navigate({ to: "/login" });
  }, [navigate, queryClient]);

  const go = useCallback((to: string, search?: Record<string, string>) => void navigate({ to, search: search as never }), [navigate]);
  useShortcuts({ palette: () => setPalette((p) => !p), help: () => setHelp(true), go, toggleTheme });
  const actions = useMemo<PaletteActions>(
    () => ({ go, toggleTheme, theme, openKillSwitch: () => setKillOpen(true), logout: () => void logout() }),
    [go, toggleTheme, theme, logout],
  );

  const toggleSidebar = () => {
    const next = !collapsed;
    setCollapsed(next);
    try {
      localStorage.setItem(SIDEBAR_KEY, next ? "collapsed" : "expanded");
    } catch {
      // Display preference only.
    }
  };

  // The sidebar sticks below the (variable-height) banner, header, and status strip.
  const top = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = top.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => document.documentElement.style.setProperty("--shell-top", `${el.offsetHeight}px`));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const liveStatus = connected && !stale;
  return (
    <div className="flex min-h-full flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-panel focus:px-3 focus:py-2">
        Skip to content
      </a>
      <div ref={top} className="safe-top sticky top-0 z-30 bg-bg">
        <EnvironmentBanner mode={status?.mode ?? null} environment={status?.environment} />
        <header className="flex h-12 items-center gap-2 border-b border-border bg-panel/95 px-3 backdrop-blur sm:gap-3 sm:px-4">
          <Button variant="ghost" size="icon" className="hidden md:inline-flex" onClick={toggleSidebar} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-pressed={!collapsed}>
            {collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
          </Button>
          <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight" aria-label="Strawberry home">
            <BrandMark className="size-6" />
            <span className="hidden text-sm sm:inline">Strawberry</span>
          </Link>
          <span
            className="flex items-center gap-1.5 text-xs"
            title={lastEventAt ? `last update ${formatTime(new Date(lastEventAt).toISOString())}` : "no updates yet"}
          >
            <span className={cn("size-2 rounded-full", liveStatus ? "bg-ok" : "bg-warn")} aria-hidden />
            <span className="text-muted">{liveStatus ? "Live status" : "Status stale"}</span>
          </span>
          <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
            <button
              type="button"
              onClick={() => setPalette(true)}
              className="hidden h-8 items-center gap-2 rounded-lg border border-border bg-panel-2 px-2.5 text-xs text-muted hover:text-fg lg:inline-flex"
              aria-label="Open command palette"
            >
              <Search className="size-3.5" aria-hidden />
              <span>Search</span>
              <Kbd>⌘K</Kbd>
            </button>
            <Button variant="ghost" size="icon" className="hidden md:inline-flex" onClick={() => setHelp(true)} aria-label="Keyboard shortcuts">
              <Keyboard />
            </Button>
            <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}>
              {theme === "dark" ? <Sun /> : <Moon />}
            </Button>
            <GlobalKillSwitch trading={status?.trading} />
            <Button variant="ghost" size="sm" className="hidden md:inline-flex" onClick={() => void logout()}>
              <LogOut />
              Log out
            </Button>
          </div>
        </header>
        <StatusStrip status={status} />
      </div>
      <div className="flex flex-1">
        <Sidebar collapsed={collapsed} />
        <main id="main" tabIndex={-1} className="min-w-0 flex-1 overflow-x-hidden p-3 pb-28 outline-none sm:p-5 md:p-6 md:pb-8">
          <Outlet />
        </main>
      </div>
      <MobileTabBar onMore={() => setMore(true)} />
      <MoreSheet open={more} onClose={() => setMore(false)} onLogout={() => void logout()} onPalette={() => setPalette(true)} />
      <CommandPalette open={palette} onClose={() => setPalette(false)} actions={actions} />
      <ShortcutHelp open={help} onClose={() => setHelp(false)} items={SHORTCUTS} />
      <EngageKillDialog open={killOpen} onClose={() => setKillOpen(false)} target={{ scope: "global", label: "all trading" }} />
    </div>
  );
}

function NavLink({ item, collapsed, onNavigate }: { item: NavItem; collapsed?: boolean; onNavigate?: () => void }) {
  const Icon = item.icon;
  return (
    <Link
      to={item.to}
      onClick={onNavigate}
      activeOptions={{ exact: item.to === "/" }}
      title={collapsed ? item.label : undefined}
      className={cn(
        "group flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13px] text-muted transition-colors hover:bg-panel-2 hover:text-fg",
        collapsed && "justify-center px-0",
      )}
      activeProps={{ className: "bg-panel-2 !text-fg font-medium shadow-[inset_2px_0_0_0_var(--color-accent)]" }}
    >
      <Icon className="size-4 shrink-0" aria-hidden />
      <span className={cn("truncate", collapsed && "sr-only")}>{item.label}</span>
    </Link>
  );
}

function Sidebar({ collapsed }: { collapsed: boolean }) {
  return (
    <nav
      aria-label="Main"
      className={cn(
        "sticky top-[var(--shell-top,0px)] hidden h-[calc(100dvh-var(--shell-top,0px))] shrink-0 self-start overflow-y-auto border-r border-border bg-panel py-3 md:block",
        collapsed ? "w-14 px-2" : "w-56 px-2.5",
      )}
    >
      {NAV_SECTIONS.map((s) => (
        <div key={s.id} className="mb-3">
          <p className={cn("px-2.5 pb-1 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-subtle", collapsed && "sr-only")}>{s.label}</p>
          <ul className="space-y-0.5">
            {navSection(s.id).map((n) => (
              <li key={n.to}>
                <NavLink item={n} collapsed={collapsed} />
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}

/** Phone tab bar: four primary pages plus "More" (UI-041). The kill switch stays in the header. */
function MobileTabBar({ onMore }: { onMore: () => void }) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const primary = mobilePrimary();
  const inMore = !primary.some((n) => (n.to === "/" ? pathname === "/" : pathname.startsWith(n.to)));
  return (
    <nav aria-label="Main mobile" className="safe-bottom fixed inset-x-0 bottom-0 z-20 grid grid-cols-5 border-t border-border bg-panel/95 backdrop-blur md:hidden">
      {primary.map((n) => {
        const Icon = n.icon;
        return (
          <Link
            key={n.to}
            to={n.to}
            activeOptions={{ exact: n.to === "/" }}
            className="flex flex-col items-center gap-0.5 py-2 text-[10.5px] text-muted"
            activeProps={{ className: "!text-fg font-semibold" }}
          >
            <Icon className="size-5" aria-hidden />
            {n.short}
          </Link>
        );
      })}
      <button type="button" onClick={onMore} className={cn("flex flex-col items-center gap-0.5 py-2 text-[10.5px]", inMore ? "font-semibold text-fg" : "text-muted")} aria-haspopup="dialog">
        <Menu className="size-5" aria-hidden />
        More
      </button>
    </nav>
  );
}

function MoreSheet({ open, onClose, onLogout, onPalette }: { open: boolean; onClose: () => void; onLogout: () => void; onPalette: () => void }) {
  const primary = new Set(mobilePrimary().map((n) => n.to));
  return (
    <Dialog open={open} onClose={onClose} title="All pages" placement="sheet">
      <nav aria-label="All pages" className="space-y-4">
        {NAV_SECTIONS.map((s) => {
          const items = navSection(s.id).filter((n) => !primary.has(n.to));
          if (items.length === 0) return null;
          return (
            <div key={s.id}>
              <p className="pb-1 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-subtle">{s.label}</p>
              <ul className="grid grid-cols-2 gap-1">
                {items.map((n) => (
                  <li key={n.to}>
                    <NavLink item={n} onNavigate={onClose} />
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </nav>
      <div className="mt-5 flex gap-2 border-t border-border pt-4">
        <Button
          variant="secondary"
          size="sm"
          onClick={() => {
            onClose();
            onPalette();
          }}
        >
          <Search />
          Search
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto"
          onClick={() => {
            onClose();
            onLogout();
          }}
        >
          <LogOut />
          Log out
        </Button>
      </div>
    </Dialog>
  );
}

function latest(a: TradingStatus | null, b: TradingStatus | null): TradingStatus | null {
  if (!a) return b;
  if (!b) return a;
  return Date.parse(a.server_time) >= Date.parse(b.server_time) ? a : b;
}
