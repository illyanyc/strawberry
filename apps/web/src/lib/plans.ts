/** Monthly user plans (USD). Quotas are product limits, not Clerk meter features. Live trading stays off (GL). */

export type PlanSlug = "free" | "research" | "live" | "desk";

export interface Plan {
  slug: PlanSlug;
  name: string;
  price: string;
  summary: string;
  strategies: number;
  backtests: number;
  analyst: number;
  liveCap: string | null;
  trial: boolean;
}

export const PLANS: readonly Plan[] = [
  {
    slug: "free",
    name: "Free",
    price: "$0",
    summary: "Paper, one strategy. Five backtests a month. No analyst runs. A live key is refused.",
    strategies: 1,
    backtests: 5,
    analyst: 0,
    liveCap: null,
    trial: false,
  },
  {
    slug: "research",
    name: "Research",
    price: "$29",
    summary: "Paper only. Five strategies, 40 backtests, 20 analyst runs. A 14-day trial does not enable live.",
    strategies: 5,
    backtests: 40,
    analyst: 20,
    liveCap: null,
    trial: true,
  },
  {
    slug: "live",
    name: "Live",
    price: "$79",
    summary: "Alpaca live after promotion. Strategy capital cap $25,000. No trial.",
    strategies: 10,
    backtests: 100,
    analyst: 60,
    liveCap: "$25,000",
    trial: false,
  },
  {
    slug: "desk",
    name: "Desk",
    price: "$149",
    summary: "Live capital cap $100,000. Email support. No phone. No hosted IBKR.",
    strategies: 25,
    backtests: 300,
    analyst: 200,
    liveCap: "$100,000",
    trial: false,
  },
];

export function planBySlug(slug: string): Plan | undefined {
  return PLANS.find((p) => p.slug === slug);
}

/** Live keys are allowed only on Live and Desk, and still need the live flag (which stays off here). */
export function planAllowsLiveKey(slug: PlanSlug): boolean {
  return slug === "live" || slug === "desk";
}
