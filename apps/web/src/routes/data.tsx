import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Database, Radio, Search } from "lucide-react";
import { useState } from "react";

import { QueryState } from "@/components/trading";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Alert, Badge, Input } from "@/components/ui/form";
import { EmptyState, Page, PageHeader } from "@/components/ui/layout";
import { api } from "@/lib/api";
import { useResolveSymbol } from "@/lib/instruments";
import { useNow } from "@/lib/events";
import { useStatus } from "@/lib/queries";
import { ageLabel, ageSeconds, type BarsResponse } from "@/lib/trading";
import { formatNs } from "@/lib/utils";

/** GET /api/v1/instruments/{id}/freshness (UI-051). */
export interface FreshnessResponse {
  instrument_id: string;
  timeframes: { timeframe: string; fresh: boolean; last_bar_close: string; reason: string; error?: string }[];
}

/**
 * Data explorer (section 14 slice 1, UI-051): look up an instrument, see per timeframe whether
 * marketdata considers its newest final bar fresh (the same rule the risk gate uses), when that bar
 * closed, and what the platform's bar cache holds; plus every feed's state and stale symbols.
 */
export function DataExplorerPage() {
  const [symbol, setSymbol] = useState("");
  const [instrument, setInstrument] = useState<{ id: string; symbol: string } | null>(null);
  const resolve = useResolveSymbol();
  const status = useStatus();
  const now = useNow();
  const fresh = useQuery({
    queryKey: ["freshness", instrument?.id],
    enabled: instrument !== null,
    queryFn: () => api<FreshnessResponse>(`/api/v1/instruments/${encodeURIComponent(instrument!.id)}/freshness`),
    refetchInterval: 30_000,
  });
  const feeds = status.data?.feeds?.feeds ?? null;

  return (
    <Page>
      <PageHeader eyebrow="Trading" title="Data explorer" description="Freshness and cached history per instrument and timeframe, and the state of every market-data feed." />
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          const s = symbol.trim().toUpperCase();
          resolve.mutate(s, { onSuccess: (id) => setInstrument({ id, symbol: s }) });
        }}
      >
        <label htmlFor="data-symbol" className="sr-only">
          Symbol
        </label>
        <Input id="data-symbol" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} placeholder="Symbol" className="w-36 font-mono uppercase" autoComplete="off" />
        <Button type="submit" variant="secondary" disabled={!symbol} busy={resolve.isPending}>
          <Search />
          Look up
        </Button>
        {instrument && (
          <Link to="/charts" search={{ symbol: instrument.symbol }} className="text-xs text-info hover:underline">
            Open {instrument.symbol} chart
          </Link>
        )}
      </form>
      {resolve.isError && <Alert>{resolve.error instanceof Error ? resolve.error.message : "Lookup failed"}</Alert>}
      <Card>
        <CardHeader icon={<Database />} title={instrument ? `${instrument.symbol} · ${instrument.id}` : "Instrument"} description="Fresh = the newest final bar closed within two bar intervals (RM-004)." />
        <CardBody className="overflow-x-auto p-0 sm:p-0">
          {!instrument ? (
            <EmptyState title="Look up a ticker to see its data" />
          ) : (
            <QueryState q={fresh} empty="" isEmpty={() => false}>
              {(d) => (
                <table className="data-table" aria-label={`Freshness of ${instrument.symbol}`}>
                  <thead>
                    <tr>
                      <th className="pl-4 sm:pl-5">Timeframe</th>
                      <th>Freshness</th>
                      <th>Last final bar closed</th>
                      <th className="num">Cached bars</th>
                      <th>Cached from</th>
                      <th className="pr-4 sm:pr-5">Detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.timeframes.map((r) => (
                      <FreshRow key={r.timeframe} id={instrument.id} r={r} now={now} />
                    ))}
                  </tbody>
                </table>
              )}
            </QueryState>
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader icon={<Radio />} title="Feeds" description="Market-data feeds as marketdata reports them." />
        <CardBody className="overflow-x-auto p-0 sm:p-0">
          {feeds === null ? (
            <div className="p-4">
              <Alert tone="warn">Feeds: unknown (marketdata unreachable)</Alert>
            </div>
          ) : feeds.length === 0 ? (
            <EmptyState title="No feeds configured" />
          ) : (
            <table className="data-table" aria-label="Feeds">
              <thead>
                <tr>
                  <th className="pl-4 sm:pl-5">Feed</th>
                  <th>State</th>
                  <th>Last message</th>
                  <th className="num">Lag (ms)</th>
                  <th className="pr-4 sm:pr-5">Stale symbols</th>
                </tr>
              </thead>
              <tbody>
                {feeds.map((f) => (
                  <tr key={f.feed}>
                    <td className="pl-4 font-mono sm:pl-5">{f.feed}</td>
                    <td>{f.connected ? <Badge tone="ok">{f.state || "connected"}</Badge> : <Badge tone="danger">disconnected</Badge>}</td>
                    <td className="text-xs text-muted">{f.connected ? `${ageLabel(ageSeconds(f.lastMessageAt, now))} ago` : formatNs(f.lastMessageAt)}</td>
                    <td className="num">{f.lagMs}</td>
                    <td className="max-w-[24rem] truncate pr-4 text-xs sm:pr-5" title={f.symbolsStale.join(", ")}>
                      {f.symbolsStale.length === 0 ? "—" : `${f.symbolsStale.length}: ${f.symbolsStale.slice(0, 12).join(", ")}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardBody>
      </Card>
      <p className="text-xs text-muted">Coverage and gap reports per backfill, data-quality findings, and the context-event timeline are Planned (they need marketdata and context-data read APIs).</p>
    </Page>
  );
}

function FreshRow({ id, r, now }: { id: string; r: FreshnessResponse["timeframes"][number]; now: number }) {
  const bars = useQuery({
    queryKey: ["bars", id, r.timeframe],
    queryFn: () => api<BarsResponse>(`/api/v1/bars?instrument_id=${encodeURIComponent(id)}&timeframe=${r.timeframe}&limit=500`),
  });
  const list = bars.data?.bars ?? [];
  return (
    <tr>
      <td className="pl-4 font-mono sm:pl-5">{r.timeframe}</td>
      <td>{r.error ? <Badge tone="warn">unknown</Badge> : r.fresh ? <Badge tone="ok">fresh</Badge> : <Badge tone="warn">stale</Badge>}</td>
      <td className="text-xs text-muted">
        {r.last_bar_close && r.last_bar_close !== "0" ? `${formatNs(r.last_bar_close)} (${ageLabel(ageSeconds(r.last_bar_close, now))} ago)` : "never"}
      </td>
      <td className="num">{bars.isPending ? "…" : bars.isError ? "—" : list.length}</td>
      <td className="text-xs text-muted">{list.length ? formatNs(list[0]!.tsOpen) : "—"}</td>
      <td className="max-w-[18rem] truncate pr-4 text-xs text-muted sm:pr-5" title={r.reason || r.error}>
        {r.error ?? (r.reason || "—")}
      </td>
    </tr>
  );
}
