import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
execFileSync("node", ["build.mjs"], { cwd: root });

const dist = join(root, "dist");
const read = (rel) => readFileSync(join(dist, rel), "utf8");

const indexable = [
  "index.html",
  "pricing/index.html",
  "product/index.html",
  "security/index.html",
  "brokers/index.html",
  "use-cases/paper/index.html",
  "use-cases/research/index.html",
  "help/index.html",
  "help/getting-started/index.html",
  "help/connect-alpaca/index.html",
  "help/connect-fmp/index.html",
  "help/ibkr/index.html",
  "help/kill-switch/index.html",
  "help/billing-and-failed-payments/index.html",
  "help/paper-and-live/index.html",
  "help/data-and-licenses/index.html",
  "changelog/index.html",
  "legal/terms/index.html",
  "legal/privacy/index.html",
  "legal/risk-disclosure/index.html",
  "legal/cookies/index.html",
  "status/index.html",
  "about/index.html",
  "contact/index.html",
];

test("indexable pages are static HTML with the logo, a title, and no forbidden claims", () => {
  const banned = ["Alpha Inertia", "lorem", "Department of State", "CRD", "287 Park", "462 7th", "phone number is", "guaranteed return", "win rate"];
  for (const rel of indexable) {
    const html = read(rel);
    assert.match(html, /<title>[^<]*Strwbrry[^<]*<\/title>/);
    assert.match(html, /index, follow/);
    assert.match(html, /logo\.svg/);
    assert.match(html, /<h1>/);
    assert.match(html, /Adwise Partners LLC/);
    assert.match(html, /228 Park Ave S PMB 849662/);
    assert.doesNotMatch(html, /aggregateRating/);
    for (const word of banned) assert.equal(html.includes(word), false, `${rel} contains ${word}`);
  }
});

test("utility pages are noindex and absent from the sitemap", () => {
  const sitemap = read("sitemap.xml");
  for (const rel of ["sign-in/index.html", "sign-up/index.html", "contact/thanks/index.html", "404.html", "500.html"]) {
    const html = read(rel);
    assert.match(html, /noindex, nofollow/);
    assert.equal(html.includes("<h1>"), true);
  }
  assert.equal(sitemap.includes("/sign-in"), false);
  assert.equal(sitemap.includes("/contact/thanks"), false);
  assert.match(read("robots.txt"), /Sitemap: https:\/\/strwbrry\.ai\/sitemap\.xml/);
  assert.match(sitemap, /<loc>https:\/\/strwbrry\.ai\/pricing<\/loc>/);
  assert.match(read("pricing/index.html"), /OfferCatalog/);
  assert.match(read("index.html"), /Paper trade, then live trade, your own account/);
  assert.match(read("index.html"), /Welcome, Ada/);
  assert.equal(read("index.html").includes("Sample layout with fixture labels"), false);
  assert.match(read("pricing/index.html"), /<table class="compare">/);
  assert.match(read("logo.svg"), /e23d4a/);
  assert.match(read("logo.svg"), /id="wordmark"/);
});
