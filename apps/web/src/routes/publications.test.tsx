import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PublicationDetailPage, PublicationsPage, type Publication } from "@/routes/publications";

const notice = "Each entry is user content. It is not advice from Adwise Partners LLC, not a live signal, and not an instruction to trade.";

const sample: Publication = {
  id: "01PUB",
  display_name: "Ada",
  title: "Opening range",
  definition: "Buy the open when the first five-minute range breaks.",
  window_start: "2020-01-02",
  window_end: "2024-12-31",
  bar_size: "5m",
  universe: "Point-in-time large-cap names",
  published_at: "2024-06-01T15:00:00Z",
  label: "user_content",
  owned: false,
};

afterEach(() => {
  vi.restoreAllMocks();
});

function renderPublications(path: string) {
  const root = createRootRoute({ component: Outlet });
  const app = createRoute({ getParentRoute: () => root, id: "app", component: Outlet });
  const index = createRoute({ getParentRoute: () => app, path: "/publications", component: PublicationsPage });
  const detail = createRoute({ getParentRoute: () => app, path: "/publications/$publicationId", component: PublicationDetailPage });
  const router = createRouter({
    routeTree: root.addChildren([app.addChildren([index, detail])]),
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("publications (UI-053)", () => {
  it("labels user content and has no trade, deploy, or copy control", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => Response.json({ sort: "published_at", notice, publications: [sample] }));
    renderPublications("/publications");
    expect(await screen.findByText("Opening range")).toBeTruthy();
    expect(screen.getByText(/not advice from Adwise Partners LLC/)).toBeTruthy();
    expect(screen.getByText("User content")).toBeTruthy();
    expect(screen.queryByRole("columnheader", { name: /return|p&l|profit|rank|sharpe/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /deploy|copy|follow|trade|order/i })).toBeNull();
    expect(screen.queryByRole("link", { name: /deploy|copy|follow|trade|order/i })).toBeNull();
  });

  it("publishes only the definition and backtest parameters", async () => {
    const bodies: unknown[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (init?.method === "POST") {
        bodies.push(JSON.parse(String(init.body)));
        return Response.json({ notice, publication: { ...sample, owned: true } }, { status: 201 });
      }
      if (url.includes("/api/v1/publications/")) {
        return Response.json({ notice, publication: { ...sample, owned: true } });
      }
      return Response.json({ sort: "published_at", notice, publications: [] });
    });
    renderPublications("/publications");
    await screen.findByRole("button", { name: "Publish" });
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Ada" } });
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Opening range" } });
    fireEvent.change(screen.getByLabelText("Window start"), { target: { value: "2020-01-02" } });
    fireEvent.change(screen.getByLabelText("Window end"), { target: { value: "2024-12-31" } });
    fireEvent.change(screen.getByLabelText("Universe description"), { target: { value: "Point-in-time large-cap names" } });
    fireEvent.change(screen.getByLabelText("Definition"), { target: { value: "Buy the open when the range breaks." } });
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({
      display_name: "Ada",
      title: "Opening range",
      definition: "Buy the open when the range breaks.",
      window_start: "2020-01-02",
      window_end: "2024-12-31",
      bar_size: "1d",
      universe: "Point-in-time large-cap names",
    });
    expect(JSON.stringify(bodies[0])).not.toMatch(/pnl|sharpe|rank|return/i);
  });

  it("shows another user's post as user content without a remove or order control", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => Response.json({ notice, publication: sample }));
    renderPublications("/publications/01PUB");
    expect(await screen.findByText(/not advice from Adwise Partners LLC/)).toBeTruthy();
    expect(screen.getByText("Buy the open when the first five-minute range breaks.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /deploy|copy|follow|trade|order|remove/i })).toBeNull();
  });
});
