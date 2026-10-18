import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { Library } from "lucide-react";
import { useState } from "react";

import { QueryState } from "@/components/trading";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Alert, Badge, Field } from "@/components/ui/form";
import { Page, PageHeader } from "@/components/ui/layout";
import { api, ApiError } from "@/lib/api";
import { formatTime } from "@/lib/utils";

/** One published strategy definition (UI-053). The server sends no profit, return, or rank. */
export interface Publication {
  id: string;
  display_name: string;
  title: string;
  definition: string;
  window_start: string;
  window_end: string;
  bar_size: string;
  universe: string;
  published_at: string;
  label: "user_content";
  owned: boolean;
}

export interface PublicationIndex {
  sort: "published_at" | "name";
  notice: string;
  publications: Publication[];
}

const BAR_SIZES = ["1m", "5m", "15m", "30m", "1h", "1d"] as const;

/**
 * Publications (UI-053): an index of user-written strategy definitions and the window, bar size,
 * and universe of a hypothetical backtest. It is not ranked by results and it cannot place an order.
 */
export function PublicationsPage() {
  const [sort, setSort] = useState<"published_at" | "name">("published_at");
  const q = useQuery({
    queryKey: ["publications", sort],
    queryFn: () => api<PublicationIndex>(`/api/v1/publications?sort=${sort}`),
  });
  return (
    <Page wide>
      <PageHeader
        eyebrow="Research"
        title="Publications"
        description="Strategy definitions other people wrote, with the dates, bar size, and universe of a hypothetical backtest. This index is ordered by time or name. It does not show profit or a rank."
      />
      <UserContentNotice notice={q.data?.notice} />
      <PublishForm />
      <Card>
        <CardHeader
          icon={<Library />}
          title="Index"
          description={sort === "name" ? "Alphabetical by title." : "Newest publication first."}
          action={
            <div className="flex gap-2">
              <Button type="button" size="sm" variant={sort === "published_at" ? "secondary" : "ghost"} onClick={() => setSort("published_at")}>
                By time
              </Button>
              <Button type="button" size="sm" variant={sort === "name" ? "secondary" : "ghost"} onClick={() => setSort("name")}>
                By name
              </Button>
            </div>
          }
        />
        <CardBody className="overflow-x-auto p-0 sm:p-0">
          <QueryState q={q} empty="Nothing has been published." isEmpty={(d) => d.publications.length === 0}>
            {(d) => (
              <table className="data-table" aria-label="Published strategy definitions">
                <thead>
                  <tr>
                    <th className="pl-4 sm:pl-5">Title</th>
                    <th>Author</th>
                    <th>Published</th>
                    <th>Bar size</th>
                    <th>Window</th>
                    <th className="pr-4 sm:pr-5">Universe</th>
                  </tr>
                </thead>
                <tbody>
                  {d.publications.map((p) => (
                    <tr key={p.id}>
                      <td className="pl-4 sm:pl-5">
                        <Link to="/publications/$publicationId" params={{ publicationId: p.id }} className="font-medium underline-offset-2 hover:underline">
                          {p.title}
                        </Link>
                        <Badge className="ml-2">User content</Badge>
                      </td>
                      <td>{p.display_name}</td>
                      <td>{formatTime(p.published_at)}</td>
                      <td className="font-mono">{p.bar_size}</td>
                      <td className="font-mono">
                        {p.window_start} – {p.window_end}
                      </td>
                      <td className="max-w-xs pr-4 sm:pr-5">{p.universe}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </QueryState>
        </CardBody>
      </Card>
    </Page>
  );
}

/** One publication. There is no control that deploys, copies, or orders it (UI-053). */
export function PublicationDetailPage() {
  const { publicationId } = useParams({ from: "/app/publications/$publicationId" });
  const queryClient = useQueryClient();
  const q = useQuery({
    queryKey: ["publication", publicationId],
    queryFn: () => api<{ notice: string; publication: Publication }>(`/api/v1/publications/${encodeURIComponent(publicationId)}`),
  });
  const withdraw = useMutation({
    mutationFn: () => api(`/api/v1/publications/${encodeURIComponent(publicationId)}`, { method: "DELETE" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["publications"] });
      void queryClient.invalidateQueries({ queryKey: ["publication", publicationId] });
    },
  });
  return (
    <Page>
      <PageHeader eyebrow="Research" title="Publication" description="A strategy definition written by a user. Reading it does not place an order." />
      <QueryState q={q} empty="This publication is not on the index." isEmpty={(d) => !d.publication}>
        {(d) => (
          <>
            <UserContentNotice notice={d.notice} />
            <Card>
              <CardHeader
                icon={<Library />}
                title={d.publication.title}
                description={`By ${d.publication.display_name} · ${formatTime(d.publication.published_at)}`}
                action={<Badge>User content</Badge>}
              />
              <CardBody className="space-y-4">
                <dl className="grid gap-3 text-sm sm:grid-cols-3">
                  <div>
                    <dt className="text-xs text-muted">Bar size</dt>
                    <dd className="font-mono">{d.publication.bar_size}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted">Window</dt>
                    <dd className="font-mono">
                      {d.publication.window_start} – {d.publication.window_end}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted">Universe</dt>
                    <dd>{d.publication.universe}</dd>
                  </div>
                </dl>
                <pre className="whitespace-pre-wrap font-sans text-sm">{d.publication.definition}</pre>
                {d.publication.owned && (
                  <Button type="button" variant="outline" disabled={withdraw.isPending} onClick={() => withdraw.mutate()}>
                    Remove from the index
                  </Button>
                )}
                {withdraw.isError && <Alert>{withdraw.error instanceof ApiError ? withdraw.error.message : "Could not remove it"}</Alert>}
              </CardBody>
            </Card>
            <p>
              <Link to="/publications" className="text-sm text-info hover:underline">
                Back to the index
              </Link>
            </p>
          </>
        )}
      </QueryState>
    </Page>
  );
}

function UserContentNotice({ notice }: { notice?: string }) {
  return (
    <Alert tone="info">
      {notice ??
        "Each entry is user content. It is not advice from Alpha Inertia LLC, not a live signal, and not an instruction to trade."}
    </Alert>
  );
}

function PublishForm() {
  const queryClient = useQueryClient();
  const [displayName, setDisplayName] = useState("");
  const [title, setTitle] = useState("");
  const [definition, setDefinition] = useState("");
  const [windowStart, setWindowStart] = useState("");
  const [windowEnd, setWindowEnd] = useState("");
  const [barSize, setBarSize] = useState<(typeof BAR_SIZES)[number]>("1d");
  const [universe, setUniverse] = useState("");
  const publish = useMutation({
    mutationFn: () =>
      api("/api/v1/publications", {
        method: "POST",
        body: {
          display_name: displayName,
          title,
          definition,
          window_start: windowStart,
          window_end: windowEnd,
          bar_size: barSize,
          universe,
        },
      }),
    onSuccess: () => {
      setTitle("");
      setDefinition("");
      void queryClient.invalidateQueries({ queryKey: ["publications"] });
    },
  });
  const ready = displayName && title && definition && windowStart && windowEnd && universe;
  return (
    <Card>
      <CardHeader title="Publish a definition" description="Rules text, plus the backtest window, bar size, and a short universe description. Results are not part of a publication." />
      <CardBody>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            publish.mutate();
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Display name" value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={40} autoComplete="nickname" />
            <Field label="Title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} />
            <Field label="Window start" type="date" value={windowStart} onChange={(e) => setWindowStart(e.target.value)} />
            <Field label="Window end" type="date" value={windowEnd} onChange={(e) => setWindowEnd(e.target.value)} />
            <div className="space-y-1.5">
              <label htmlFor="pub-bar" className="block text-xs font-medium text-muted">
                Bar size
              </label>
              <select
                id="pub-bar"
                className="h-10 w-full rounded-lg border border-border bg-bg px-3 text-sm"
                value={barSize}
                onChange={(e) => {
                  const v = e.target.value;
                  if (v === "1m" || v === "5m" || v === "15m" || v === "30m" || v === "1h" || v === "1d") setBarSize(v);
                }}
              >
                {BAR_SIZES.map((b) => (
                  <option key={b} value={b}>
                    {b}
                  </option>
                ))}
              </select>
            </div>
            <Field label="Universe description" value={universe} onChange={(e) => setUniverse(e.target.value)} maxLength={240} />
          </div>
          <div className="space-y-1.5">
            <label htmlFor="pub-definition" className="block text-xs font-medium text-muted">
              Definition
            </label>
            <textarea
              id="pub-definition"
              className="min-h-28 w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm"
              value={definition}
              maxLength={8000}
              onChange={(e) => setDefinition(e.target.value)}
            />
          </div>
          <Button type="submit" variant="secondary" disabled={!ready || publish.isPending} busy={publish.isPending}>
            Publish
          </Button>
          {publish.isError && <Alert>{publish.error instanceof ApiError ? publish.error.message : "Could not publish"}</Alert>}
        </form>
      </CardBody>
    </Card>
  );
}
