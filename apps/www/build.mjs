import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "../..");
const dist = join(here, "dist");
const site = "https://strwbrry.ai";
const app = "https://app.strwbrry.ai";
const email = "support@strwbrry.ai";
const company = "Adwise Partners LLC";
const address = "228 Park Ave S PMB 849662, New York, New York 10003";
const logo = readFileSync(join(repo, "brand/logo.svg"), "utf8");
const css = readFileSync(join(here, "site.css"), "utf8");

const footerLinks = [
  ["/pricing", "Pricing"],
  ["/help", "Help"],
  ["/changelog", "Changelog"],
  ["/security", "Security"],
  ["/legal/terms", "Terms"],
  ["/legal/privacy", "Privacy"],
  ["/legal/risk-disclosure", "Risk disclosure"],
  ["/status", "Status"],
  ["/about", "About"],
  ["/contact", "Contact"],
];

function esc(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function titleOf(topic) {
  return topic.includes("Strwbrry") ? topic : `${topic} · Strwbrry`;
}

function crumbs(items) {
  if (!items?.length) return "";
  const parts = items.map((c, i) =>
    i === items.length - 1 ? esc(c.label) : `<a href="${esc(c.href)}">${esc(c.label)}</a>`,
  );
  return `<nav class="crumbs" aria-label="Breadcrumb">${parts.join(" / ")}</nav>`;
}

function layout(page) {
  const index = page.index ? "index, follow" : "noindex, nofollow";
  const path = page.path === "/" ? "/" : page.path;
  const url = site + (path === "/" ? "/" : path);
  const nav = [
    ["/product", "Product"],
    ["/pricing", "Pricing"],
    ["/brokers", "Brokers"],
    ["/security", "Security"],
    ["/help", "Help"],
  ]
    .map(([href, label]) => `<a href="${href}"${page.path === href ? ' aria-current="page"' : ""}>${label}</a>`)
    .join("");
  const schema = page.schema ? `<script type="application/ld+json">${JSON.stringify(page.schema)}</script>` : "";
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>${esc(titleOf(page.title))}</title>
  <meta name="description" content="${esc(page.description)}" />
  <meta name="robots" content="${index}" />
  <link rel="canonical" href="${esc(url)}" />
  <link rel="alternate" hreflang="en" href="${esc(url)}" />
  <link rel="icon" href="/logo.svg" type="image/svg+xml" />
  <meta property="og:title" content="${esc(titleOf(page.title))}" />
  <meta property="og:description" content="${esc(page.description)}" />
  <meta property="og:url" content="${esc(url)}" />
  <meta property="og:image" content="${site}/og.svg" />
  <meta property="og:type" content="website" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="${esc(titleOf(page.title))}" />
  <meta name="twitter:description" content="${esc(page.description)}" />
  <meta name="twitter:image" content="${site}/og.svg" />
  <style>${css}</style>
  ${schema}
</head>
<body>
  <a class="skip" href="#main">Skip to content</a>
  <header class="site">
    <div class="wrap">
      <a class="brand" href="/" aria-label="Strwbrry home">${logo}<svg class="wordmark" viewBox="0 0 156 32" aria-hidden="true"><use href="#wordmark"/></svg></a>
      <nav class="primary" aria-label="Primary">${nav}</nav>
      <div class="nav-cta">
        <a class="btn" href="${app}/sign-in">Sign in</a>
        <a class="btn primary" href="${app}/sign-up">Create a free account</a>
      </div>
    </div>
  </header>
  <main id="main">
    <div class="wrap">
      ${crumbs(page.crumbs)}
      ${page.body}
    </div>
  </main>
  <footer class="site">
    <div class="wrap">
      <nav aria-label="Footer">${footerLinks.map(([href, label]) => `<a href="${href}">${label}</a>`).join("")}</nav>
      <p>${esc(company)}. ${esc(address)}. <a href="mailto:${email}">${email}</a>.</p>
      <p class="note">Strwbrry is software you configure on your own brokerage account. It is not a broker and it does not hold customer funds.</p>
    </div>
  </footer>
</body>
</html>
`;
}

const org = {
  "@type": "Organization",
  name: company,
  url: site,
  email,
  address: {
    "@type": "PostalAddress",
    streetAddress: "228 Park Ave S PMB 849662",
    addressLocality: "New York",
    addressRegion: "New York",
    postalCode: "10003",
    addressCountry: "US",
  },
};

function webPage(name) {
  return { "@context": "https://schema.org", "@type": "WebPage", name, url: site, isPartOf: { "@type": "WebSite", name: "Strwbrry", url: site } };
}

const helpArticles = [
  {
    path: "/help/getting-started",
    title: "Getting started",
    description: "Create an account, enroll an authenticator app, and open an empty paper dashboard.",
    cta: ["Connect a paper account", `${app}/connections`],
    html: `<p>A new Strwbrry account starts empty. There is no sample portfolio and no borrowed broker key.</p>
<ol>
<li>Open the app and create an account. Confirm the email address Clerk sends.</li>
<li>Enroll an authenticator app and save backup codes before the session becomes active. SMS is not a factor.</li>
<li>Read the risk disclosure and accept it. The free plan does not ask for a card.</li>
<li>Land on the dashboard. It shows paper mode, no strategies, and no broker connection until you add one.</li>
</ol>
<p>The self-hosted install on a machine you control still uses a passkey and a setup token. That path is not this public signup.</p>`,
  },
  {
    path: "/help/connect-alpaca",
    title: "Connect Alpaca",
    description: "Paste an Alpaca key once. It is encrypted at rest and not shown again. Paper and live keys are separate.",
    cta: ["Open connections", `${app}/connections`],
    html: `<p>Connections has two slots: paper and live. A paper key cannot be saved into the live slot.</p>
<ol>
<li>In the Alpaca dashboard, create a key for the paper account. Copy the key id and the secret.</li>
<li>In Strwbrry, open Connections, choose the paper slot, and paste both values.</li>
<li>Confirm with a fresh second factor. The browser sends the secret once. It is not written to local storage.</li>
<li>The page then shows the environment, a last-four hint, and the time the key was stored. The secret is not shown again.</li>
</ol>
<p>A live key is refused unless the plan is Live or Desk, the risk disclosure is accepted, and live trading has been turned on for that strategy through the promotion checks. This deployment does not turn live trading on by connecting a key.</p>
<p>Rotate by pasting a new secret after the same second-factor check. Delete is refused while that account still has an open strategy order or a non-zero strategy position, unless a kill switch is already latched and you confirm you will flatten at the broker yourself. Strwbrry does not flatten the account as part of delete.</p>`,
  },
  {
    path: "/help/connect-fmp",
    title: "Connect a Financial Modeling Prep key",
    description: "Use your own FMP key for fundamentals and calendars. A personal FMP plan cannot be shared inside a multi-user product.",
    cta: ["Open keys", `${app}/settings/keys`],
    html: `<p>Financial Modeling Prep is optional. Strwbrry does not lend you a platform key, and it does not show FMP fields to an account that has not saved its own key.</p>
<p>A personal FMP license is for one person. It does not allow the key to be shared inside a product other people use. Paste your own key under API keys. The field is write-only: the secret is sealed and is not displayed again. The page stores a short hint and the time of the write.</p>
<p>The key is for your research jobs. It is not a broker credential and it cannot place an order.</p>`,
  },
  {
    path: "/help/ibkr",
    title: "Interactive Brokers",
    description: "Hosted Strwbrry does not connect IBKR. The desktop app needs a weekly two-factor login that we will not operate for you.",
    cta: ["Read the brokers page", "/brokers"],
    html: `<p>Hosted Strwbrry does not offer an Interactive Brokers connection. There is no Connect IBKR button on the hosted connections screen.</p>
<p>The IBKR desktop gateway needs a weekly human two-factor approval and a session on a machine the account holder controls. Operating that login for a stranger would mean holding a password and watching a recovery screen. We do not do that.</p>
<p>If you want IBKR, run the self-hosted stack on a computer you administer. That path is separate from the hosted signup. There is no date for a hosted IBKR connection.</p>`,
  },
  {
    path: "/help/kill-switch",
    title: "Kill switch",
    description: "Halt trading from any screen. Releasing a halt needs a fresh second factor on a desktop. Halting does not.",
    cta: ["Open the risk page", `${app}/risk`],
    html: `<p>The kill switch is in the header on every signed-in screen, including a phone. Halting takes two taps: the button, then confirm. There is no authenticator prompt on the way in. A paywall cannot hide the halt button.</p>
<p>A halt freezes new orders and cancels open orders that are not protective stops. Protective stops stay. Manual holdings are not closed.</p>
<p>Releasing the latch is desktop only. It needs a fresh second factor from an authenticator app. Email, a password, and a phone code do not qualify. A phone can halt. It cannot release.</p>`,
  },
  {
    path: "/help/billing-and-failed-payments",
    title: "Billing and failed payments",
    description: "A failed payment stops new trades. It does not block exits, cancels, or the kill switch, and it does not flatten the account.",
    cta: ["Open billing", `${app}/settings/billing`],
    html: `<p>Plans are monthly and in US dollars. Tax is not included. The free plan stays available if you cancel or stop paying.</p>
<p>When a payment is past due, Strwbrry freezes new entries for that account: no new opens, no raises, no live promotion. These keep working:</p>
<ul>
<li>Seeing your own positions, open orders, and recent fills.</li>
<li>The kill switch.</li>
<li>Canceling your own orders.</li>
<li>Exiting or reducing your own strategy quantity, still through the risk check.</li>
<li>Reconciliation status, security alerts, and your own audit log.</li>
<li>Replacing a card or opening the pricing table.</li>
</ul>
<p>A failed payment does not flatten the account, does not cancel protective stops, and does not delete broker keys. When the subscription ends, the same freeze remains. Paper strategies may continue inside the free plan's one-strategy cap. Live strategies do not keep opening.</p>`,
  },
  {
    path: "/help/paper-and-live",
    title: "Paper and live",
    description: "Live trading is off until you promote a strategy. A risk breach can send it back to paper. Turning it on again is manual.",
    cta: ["See the Live plan", "/pricing"],
    html: `<p>Every strategy starts in paper. Paper and live use different keys, different endpoints, and different processes. A paper key cannot place a live order.</p>
<p>Live mode stays off until you promote a strategy. Promotion needs a paper track record, a fresh second factor, the global live flag, and a capital cap on the plan. Research and Free cannot save a live key. The Live plan's cap is $25,000 of allocated strategy capital. Desk is $100,000. The cap is the sum of allocations, not the broker's net liquidation.</p>
<p>A risk breach or a reconciliation break can send a strategy back to paper. Turning it live again is a manual promotion. Nothing in billing, signup, or a key paste turns live trading on.</p>`,
  },
  {
    path: "/help/data-and-licenses",
    title: "Data and licenses",
    description: "Market data shown in the product comes from connections you add, unless a separate data license says otherwise.",
    cta: ["Security page", "/security"],
    html: `<p>On the hosted product, bars and account data are requested with the Alpaca key you connect. They are not taken from someone else's subscription and resold to you.</p>
<p>Fundamentals and calendars from Financial Modeling Prep appear only after you save your own key. A personal FMP plan is not a license for a multi-user product, so Strwbrry does not call FMP with a house key.</p>
<p>Orders, fills, positions, and models trained on your decisions stay on your account. They are not a public dataset. There is no page that ranks accounts by money made.</p>`,
  },
];

const plans = [
  ["Free", "0"],
  ["Research", "29"],
  ["Live", "79"],
  ["Desk", "149"],
];

const dashboard = `<figure class="dash-figure">
  <div class="dash">
    <div class="dash-banner">PAPER · ENV: PAPER</div>
    <div class="dash-top">
      <svg class="mark" viewBox="0 0 64 64" aria-hidden="true"><path d="M32 14 47 21 54 32 47 49 32 56 17 49 10 32 17 21Z" fill="#e23d4a"/><rect x="30" y="2" width="4" height="12" fill="#8fbf9f"/><polygon points="30,9 15,3 19,14" fill="#8fbf9f"/><polygon points="34,9 49,3 45,14" fill="#8fbf9f"/><g fill="#1e1e1e"><rect x="26" y="23" width="6" height="6"/><rect x="36" y="23" width="6" height="6"/><rect x="31" y="33" width="6" height="6"/><rect x="26" y="43" width="6" height="6"/><rect x="36" y="43" width="6" height="6"/></g></svg>
      <span class="name">Strwbrry</span>
      <span class="stale">Status stale</span>
      <span class="spacer"></span>
      <span class="kill">Kill switch</span>
    </div>
    <div class="dash-status">
      <span>Running</span><span>alpaca-paper · ready</span><span>recon ok</span><span>0 open orders</span>
    </div>
    <div class="dash-body">
      <aside class="dash-side" aria-hidden="true">
        <p>Overview</p>
        <ul><li class="on">Dashboard</li><li>Set up</li><li>Portfolio overview</li></ul>
        <p>Trading</p>
        <ul><li>Portfolio</li><li>Strategies</li><li>Orders</li><li>Charts</li></ul>
        <p>Operations</p>
        <ul><li>Risk &amp; kill switches</li><li>Monitoring</li></ul>
      </aside>
      <div class="dash-main">
        <p class="dash-kicker">Sunday, October 18</p>
        <h2>Welcome, Ada</h2>
        <p class="sub">Accounts, strategies, and trading state at a glance. Manual holdings and each strategy's virtual portfolio are shown separately.</p>
        <div class="dash-actions"><span>Connections</span><span>Charts</span><span class="go">Manual order</span></div>
        <div class="dash-grid">
          <div>
            <h3>Accounts</h3>
            <div class="acct">
              <div class="row"><span>alpaca-paper</span><span class="pill">RECON OK</span></div>
              <p class="note">Equity</p>
              <p class="eq">25000.00</p>
              <div class="metrics"><span>Cash<b>18420.15</b></span><span>Buying power<b>18420.15</b></span><span>Manual day P&amp;L<b>0.00</b></span></div>
            </div>
            <div class="pane stack">
              <h3>Strategies</h3>
              <p class="note">Each strategy trades only its own virtual portfolio.</p>
              <div class="row"><strong>Opening range (sample)</strong><span class="pill paper">PAPER</span></div>
              <p class="note">alpaca-paper · day</p>
              <svg viewBox="0 0 240 48" width="100%" height="48" aria-hidden="true"><polyline fill="none" stroke="#3fa266" stroke-width="1.5" points="0,36 36,34 72,38 108,28 144,22 180,24 240,12"/></svg>
              <dl class="strat"><dt>Allocation</dt><dd>5000.00</dd><dt>Equity</dt><dd>5128.40</dd><dt>Day P&amp;L</dt><dd class="up">+18.40</dd></dl>
            </div>
          </div>
          <div class="pane">
            <h3>Trading state</h3>
            <p><span class="pill">RUNNING</span> <span class="pill paper">PAPER</span> 0 open orders</p>
            <p class="note">No kill switch or halt is active.</p>
          </div>
        </div>
      </div>
    </div>
    <div class="tabs" aria-hidden="true"><span class="on">Home</span><span>Portfolio</span><span>Strategies</span><span>Charts</span><span>More</span></div>
  </div>
  <figcaption class="note">Sample paper session. Not a customer account. Live trading is off.</figcaption>
</figure>`;

const pages = [];

pages.push({
  path: "/",
  title: "Strwbrry — paper trade, then live trade, your own account",
  description: "Research and automate US stock strategies on your own brokerage account. Paper first. A kill switch on every screen. You keep custody.",
  index: true,
  schema: {
    "@context": "https://schema.org",
    "@graph": [
      { "@type": "WebSite", name: "Strwbrry", url: site },
      org,
      { "@type": "SoftwareApplication", name: "Strwbrry", applicationCategory: "FinanceApplication", operatingSystem: "Web" },
    ],
  },
  body: `<p class="eyebrow">US stocks and ETFs</p>
<h1>Paper trade, then live trade, your own account.</h1>
<p class="lede">Strwbrry is software that researches and runs strategies on a brokerage account you connect. You set the capital cap. You can halt it. Strwbrry does not hold the cash or the securities.</p>
<p><a class="btn primary" href="${app}/sign-up">Create a free account</a> <a class="btn" href="/pricing">View pricing</a></p>
${dashboard}
<div class="grid">
  <section class="card"><h3>Virtual portfolios</h3><p>Each strategy trades only the quantity in its own book. It cannot sell a position that belongs to you or to another strategy.</p></section>
  <section class="card"><h3>Risk check before every order</h3><p>No order is sent to a broker without a recorded risk decision. There is no bypass from the dashboard.</p></section>
  <section class="card"><h3>Kill switch on every screen</h3><p>Halt from the header, including on a phone. Releasing the halt needs a fresh second factor on a desktop.</p></section>
</div>
<p class="note">Read the <a href="/legal/risk-disclosure">risk disclosure</a> before you connect a live account. Paper results are not live results.</p>`,
});

pages.push({
  path: "/pricing",
  title: "Pricing",
  description: "Free paper trading. Research is $29 a month. Live Alpaca automation is $79 a month with a capital cap. Canceling does not flatten your positions.",
  index: true,
  schema: {
    "@context": "https://schema.org",
    "@type": "OfferCatalog",
    name: "Strwbrry plans",
    itemListElement: plans.map(([name, price]) => ({ "@type": "Offer", name, price, priceCurrency: "USD" })),
  },
  body: `<h1>Pricing</h1>
<p class="lede">Monthly, in US dollars. Tax is not included. Annual billing is off. A failed payment stops new entries. It does not flatten positions or disable the kill switch.</p>
<div class="compare-wrap">
<table class="compare">
  <thead><tr><th></th><th>Free</th><th>Research</th><th>Live</th><th>Desk</th></tr></thead>
  <tbody>
    <tr><th>Price</th><td class="num">$0</td><td class="num">$29</td><td class="num">$79</td><td class="num">$149</td></tr>
    <tr><th>Strategies</th><td class="num">1</td><td class="num">5</td><td class="num">10</td><td class="num">25</td></tr>
    <tr><th>Backtests / month</th><td class="num">5</td><td class="num">40</td><td class="num">100</td><td class="num">300</td></tr>
    <tr><th>Analyst runs</th><td class="num">0</td><td class="num">20</td><td class="num">60</td><td class="num">200</td></tr>
    <tr><th>Live key</th><td>No</td><td>No</td><td>Yes</td><td>Yes</td></tr>
    <tr><th>Capital cap</th><td>—</td><td>—</td><td class="num">$25,000</td><td class="num">$100,000</td></tr>
    <tr><th>Trial</th><td>—</td><td>14 days</td><td>—</td><td>—</td></tr>
    <tr><th>Support</th><td>—</td><td>—</td><td>—</td><td>Email</td></tr>
  </tbody>
</table>
</div>
<p><a class="btn primary" href="${app}/sign-up">Start Research trial</a> <a class="btn" href="${app}/sign-up">Stay on Free</a></p>
<p class="note">The Research trial needs a card when billing is connected and does not enable live trading. A live key still needs a promotion, a second factor, and the live flag. Unused runs do not roll over. Card numbers stay with the card processor. See <a href="/help/billing-and-failed-payments">what a failed payment does</a>. Hosted IBKR is not included.</p>`,
});

pages.push({
  path: "/product",
  title: "How Strwbrry trades",
  description: "Strategies trade only their own quantity. Backtests use the same features as live. Live stays off until you promote a strategy.",
  index: true,
  schema: webPage("How Strwbrry trades"),
  body: `<h1>How Strwbrry trades</h1>
<p class="lede">The loop is research, backtest, paper, then live. The feature code is the same in each step. Live stays off until you promote a strategy.</p>
<ol class="prose">
<li><strong>Research.</strong> You define a strategy and train inside a quota. The sealed holdout is not a tuning set.</li>
<li><strong>Backtest.</strong> Fills are simulated no earlier than the next tradable moment after the decision. They are not filled on the signal bar.</li>
<li><strong>Paper.</strong> The strategy runs against a paper account with its own key. Paper is not a promise about live results.</li>
<li><strong>Live.</strong> You promote one strategy. It receives a capital cap. Orders carry an attribution id so the fill is booked to that strategy's virtual portfolio.</li>
</ol>
<p>A strategy cannot close a quantity it does not own. Manual holdings stay manual. The analyst can propose a change. It cannot promote a strategy to live.</p>
<p><a class="btn primary" href="/security">Read the security page</a> <a class="btn" href="/pricing">Pricing</a></p>`,
});

pages.push({
  path: "/security",
  title: "Security",
  description: "Broker keys are encrypted per account. The browser never receives them. Live trading and key changes require a fresh second factor.",
  index: true,
  schema: webPage("Security"),
  body: `<h1>Security</h1>
<p class="lede">Keys are encrypted per account. The browser never receives a stored secret. Live trading and key changes require a fresh second factor.</p>
<div class="prose">
<p>Alpaca and FMP secrets are sealed with a data key for that record. The page that saved them cannot read them back. Status shows a hint and a timestamp.</p>
<p>Hosted signup uses an email-verified account and an authenticator app before the session is active. SMS is off. A kill switch does not ask for that factor. Releasing a halt, saving a key, and promoting a strategy do.</p>
<p>One account's session does not authorize another account's orders, positions, or keys. A breach of one account must not decrypt another account's secrets: the authenticated data on each record includes the account id and the environment. A paper record does not decrypt as live.</p>
<p>We cannot promise that a compromised master key, a compromised broker account, or a broker outage is harmless. If the broker is down, the kill switch in Strwbrry cannot reach it. Read the <a href="/legal/risk-disclosure">risk disclosure</a>.</p>
</div>`,
});

pages.push({
  path: "/brokers",
  title: "Alpaca, FMP, and IBKR",
  description: "Connect your own Alpaca account from the dashboard. Bring an FMP key if you have one. Hosted accounts do not include Interactive Brokers.",
  index: true,
  schema: webPage("Alpaca, FMP, and IBKR"),
  body: `<h1>Alpaca, FMP, and IBKR</h1>
<p class="lede">Hosted Strwbrry connects to your Alpaca account. You may add your own Financial Modeling Prep key. Hosted accounts do not include Interactive Brokers.</p>
<div class="prose">
<p><strong>Alpaca.</strong> Paper and live are separate keys. The connections screen has a slot for each. Market data for your account is requested with your key. There is no house Alpaca key on the order path.</p>
<p><strong>FMP.</strong> Optional, and only the key you paste. We do not display a platform key and we do not share a personal FMP license across accounts.</p>
<p><strong>IBKR.</strong> Not offered on the hosted product. The gateway needs a weekly human two-factor login and a desktop session. We will not host that for strangers. The self-hosted stack remains the path for an IBKR account you run yourself. See <a href="/help/ibkr">the IBKR article</a>.</p>
</div>
<p><a class="btn primary" href="${app}/sign-up">Connect Alpaca</a></p>`,
});

pages.push({
  path: "/use-cases/paper",
  title: "Paper trading",
  description: "Every strategy starts in paper. Paper uses separate credentials from live. Promotion to live is manual.",
  index: true,
  schema: webPage("Paper trading"),
  body: `<h1>Paper trading</h1>
<p class="lede">Every strategy starts in paper. Paper uses its own credentials. It is not a forecast of live fills.</p>
<div class="prose">
<p>On the free plan you can run one paper strategy, look at charts, orders, and risk, and use the kill switch. Research raises the paper quota. Neither plan accepts a live key.</p>
<p>A strategy moves to live only when you promote it. Demotion back to paper can be automatic after a risk breach. Promotion the other way is manual.</p>
</div>
<p><a class="btn primary" href="${app}/sign-up">Create a free account</a></p>`,
});

pages.push({
  path: "/use-cases/research",
  title: "Research and backtests",
  description: "Train and backtest on point-in-time data. The sealed holdout is not a tuning set. The analyst proposes; you approve.",
  index: true,
  schema: webPage("Research and backtests"),
  body: `<h1>Research and backtests</h1>
<p class="lede">Training and backtests use point-in-time data. The sealed holdout is not a tuning set. The analyst proposes; you approve.</p>
<div class="prose">
<p>Features are computed from data that was available at the decision time. A backtest does not fill on the bar that produced the signal.</p>
<p>Quotas are hard stops: backtests and analyst runs do not roll over, and they do not meter into a surprise invoice. The analyst cannot promote a strategy to live and cannot raise its own autonomy without you.</p>
</div>
<p><a class="btn primary" href="/pricing">See Research pricing</a></p>`,
});

pages.push({
  path: "/help",
  title: "Help",
  description: "Setup, connecting Alpaca, billing, the kill switch, and what happens if a payment fails.",
  index: true,
  schema: { "@context": "https://schema.org", "@type": "CollectionPage", name: "Strwbrry help", url: `${site}/help` },
  body: `<h1>Help</h1>
<p class="lede">Setup, Alpaca, billing, the kill switch, and failed payments. Search looks at this list in the browser. It does not crawl the trading app.</p>
<label>Search articles<input id="help-q" type="search" placeholder="Alpaca, kill switch, payment" /></label>
<p id="help-empty" class="note" hidden>No article matches that search.</p>
<div class="grid" id="help-list">
${helpArticles
  .map(
    (a) => `<article class="card" data-article><h3><a href="${a.path}">${esc(a.title)}</a></h3><p>${esc(a.description)}</p></article>`,
  )
  .join("")}
</div>
<p><a class="btn primary" href="/help/getting-started">Getting started</a></p>
<script src="/help.js"></script>`,
});

for (const a of helpArticles) {
  pages.push({
    path: a.path,
    title: a.title,
    description: a.description,
    index: true,
    crumbs: [
      { href: "/help", label: "Help" },
      { href: a.path, label: a.title },
    ],
    schema: {
      "@context": "https://schema.org",
      "@type": "TechArticle",
      headline: a.title,
      description: a.description,
      datePublished: "2026-10-18",
      author: { "@type": "Organization", name: company },
    },
    body: `<h1>${esc(a.title)}</h1><div class="prose">${a.html}</div><p><a class="btn primary" href="${esc(a.cta[1])}">${esc(a.cta[0])}</a></p>
<p class="note">Related: <a href="/help/paper-and-live">Paper and live</a> · <a href="/help/billing-and-failed-payments">Billing and failed payments</a> · <a href="/help/connect-alpaca">Connect Alpaca</a></p>`,
  });
}

const changelog = [
  {
    date: "2026-10-18",
    title: "Public site",
    html: "<p>The marketing site, pricing, and help center are public. The trading app is a separate origin and is not indexed. Signup and monthly plans use Clerk when keys are configured, and a local mock when they are not. The mock does not charge a card and does not enable live trading. Live trading stays off. There is no cross-user board of money made.</p>",
  },
];

pages.push({
  path: "/changelog",
  title: "Changelog",
  description: "What changed in Strwbrry, by date.",
  index: true,
  schema: {
    "@context": "https://schema.org",
    "@type": "CollectionPage",
    name: "Strwbrry changelog",
    hasPart: changelog.map((c) => ({ "@type": "TechArticle", headline: c.title, datePublished: c.date })),
  },
  body: `<h1>Changelog</h1>
<p class="lede">Product changes, by date. This list does not discuss markets.</p>
${changelog
  .map(
    (c) => `<article class="card"><p class="eyebrow">${esc(c.date)}</p><h2>${esc(c.title)}</h2>${c.html}</article>`,
  )
  .join("")}
<p class="note">When a change needs a click, the matching <a href="/help">help article</a> is updated in the same release.</p>`,
});

const draft = `<p class="draft">Draft for counsel. This is not a binding legal text until Adwise Partners LLC adopts a version a lawyer has revised. It describes the software product. It is not legal advice.</p>`;

pages.push({
  path: "/legal/terms",
  title: "Terms of service",
  description: "The contract for using Strwbrry.",
  index: true,
  schema: webPage("Terms of service"),
  body: `${draft}<h1>Terms of service</h1>
<div class="prose">
<p>These terms are between you and ${esc(company)}, ${esc(address)}. Notices and questions go to <a href="mailto:${email}">${email}</a>. There is no phone number for the service.</p>
<p>Strwbrry is software you configure. You can research US stocks and ETFs, write strategy rules, and run paper trading on an account you connect. We do not hold your cash or your securities. We do not choose what you buy or sell. An order, when live trading is offered, goes to your broker with your credentials.</p>
<p>You must be able to form a contract. The hosted service is for individuals in the United States. Quotas on the pricing page are hard stops. They are not a promise of fills or of results.</p>
<p>Acceptable use: you will not share a broker key, you will not use the service for someone else's account, and you will not publish a return, a rank, or an instruction for someone else to trade. Your broker's terms also apply. If you add a Financial Modeling Prep key, that vendor's terms apply to the data you bring. We do not lend you our vendor keys.</p>
<p>You may stop paying. Cancellation does not close your broker account and does not flatten positions. We may close a software account that we believe is outside these terms, and we will not liquidate a broker account to do it. Account deletion is refused while a strategy quantity is open or an order is working.</p>
<p>The service is provided as software with the limits in the <a href="/legal/risk-disclosure">risk disclosure</a>. These terms do not turn live trading on.</p>
</div>`,
});

pages.push({
  path: "/legal/privacy",
  title: "Privacy policy",
  description: "What Strwbrry stores, who processes identity and cards, and how long order records are kept.",
  index: true,
  schema: webPage("Privacy policy"),
  body: `${draft}<h1>Privacy policy</h1>
<div class="prose">
<p>${esc(company)} (${esc(address)}) operates Strwbrry. Privacy questions go to <a href="mailto:${email}">${email}</a>.</p>
<p>We store an account (name and email from the identity processor), the strategies you write, backtests you run, and, if you connect one, an encrypted broker or data key. We do not sell personal information and we do not sell positions.</p>
<p>Clerk processes hosted identity. Stripe processes the card through Clerk Billing when billing is on. We do not store the card number. We store the plan name, status, and amounts.</p>
<p>Broker and API secrets are encrypted at rest and are not shown again after you paste them. Order, fill, risk, and key-change metadata are kept so the audit record stays intact. The working default for those records is seven years. That period is a product default, not tax advice. Secrets are deleted when you delete the connection or close the account, after the open-position check.</p>
<p>Closing the account is refused while a strategy quantity or an open order remains. We do not auto-liquidate to make deletion possible. A published strategy definition, if you publish one, shows the definition and the non-performance facts of a hypothetical backtest. It does not show profit, return, rank, your key, or your positions. There is no leaderboard of money made.</p>
<p>The marketing site at ${site} does not set analytics cookies. The app origin keeps its own session. See the <a href="/legal/cookies">cookie notice</a>.</p>
</div>`,
});

pages.push({
  path: "/legal/risk-disclosure",
  title: "Risk disclosure",
  description: "Trading can lose money. Paper results are not live results. Read this before connecting a live account.",
  index: true,
  schema: webPage("Risk disclosure"),
  body: `${draft}<h1>Risk disclosure</h1>
<div class="prose">
<p>Trading can lose money, including more than you expect from a paper run. Paper results are not live results. A backtest is a simulation. It is not a promise that an order will fill, or at what price.</p>
<p>The software can fail. Orders can be rejected. Data can be stale. A broker can be down. A model can be wrong. If the broker is unreachable, the kill switch in Strwbrry does not reach the broker. Protective stops that were already accepted can still fail at the broker.</p>
<p>You direct the strategies. Strwbrry does not manage assets and does not promise a gain or a comparison with an index. Acceptance of this disclosure is stored before a live key can be saved. This public page does not itself accept the disclosure. That control is in the app, after you sign in.</p>
</div>`,
});

pages.push({
  path: "/legal/cookies",
  title: "Cookie notice",
  description: "Cookies used on this site.",
  index: true,
  schema: webPage("Cookie notice"),
  body: `<h1>Cookie notice</h1>
<div class="prose">
<p>This marketing site (${site}) does not set analytics cookies, advertising cookies, or a session cookie. Pages are static files.</p>
<p>The trading app on ${app} is a different site. It keeps an HttpOnly session cookie so a signed-in browser stays signed in. That cookie is not set when you read these pages. The app is not indexed.</p>
<p>If a later version of this site sets a cookie, this notice will name it before that version ships.</p>
</div>`,
});

pages.push({
  path: "/status",
  title: "Status",
  description: "Current status of the Strwbrry service.",
  index: true,
  schema: webPage("Status"),
  body: `<h1>Status</h1>
<p class="lede">Component health for the Strwbrry service. This is not your broker's status, and it does not include account results.</p>
<table>
<thead><tr><th>Component</th><th>State</th></tr></thead>
<tbody>
<tr><td>App</td><td class="status-ok">No incident recorded</td></tr>
<tr><td>Gateway</td><td class="status-ok">No incident recorded</td></tr>
<tr><td>Billing webhooks</td><td class="status-ok">No incident recorded</td></tr>
<tr><td>Job runner</td><td class="status-ok">No incident recorded</td></tr>
</tbody>
</table>
<p class="note">This page ships with the site. It is a public snapshot, not a live probe, and it does not name a customer.</p>
<h2>Incidents</h2>
<p>No incidents recorded.</p>
<h2>Subscribe</h2>
<form action="/status/subscribed" method="post">
<label>Email<input type="email" name="email" autocomplete="email" required /></label>
<button class="btn primary" type="submit">Subscribe to updates</button>
</form>`,
});

pages.push({
  path: "/about",
  title: "About",
  description: "Who operates Strwbrry.",
  index: true,
  schema: {
    "@context": "https://schema.org",
    "@graph": [{ "@type": "AboutPage", name: "About Strwbrry", url: `${site}/about` }, org],
  },
  body: `<h1>About</h1>
<div class="prose">
<p>Strwbrry is operated by ${esc(company)}, ${esc(address)}. The company provides software. It is not a fund. This page does not list a fictional team.</p>
<p>Contact: <a href="mailto:${email}">${email}</a>. There is no phone number.</p>
<p><a href="/contact">Contact</a></p>
</div>`,
});

pages.push({
  path: "/contact",
  title: "Contact",
  description: "Contact Strwbrry for security, privacy, and billing.",
  index: true,
  schema: { "@context": "https://schema.org", "@type": "ContactPage", name: "Contact Strwbrry", url: `${site}/contact` },
  body: `<h1>Contact</h1>
<p class="lede">Security reports, privacy questions, and billing questions go to <a href="mailto:${email}">${email}</a>. That mailbox is the security contact. This form does not open a trading account.</p>
<form action="/contact/thanks" method="post">
<label>Name<input name="name" autocomplete="name" required /></label>
<label>Email<input type="email" name="email" autocomplete="email" required /></label>
<label>Topic<select name="topic"><option>Security</option><option>Privacy</option><option>Billing</option><option>Something else</option></select></label>
<label>Message<textarea name="message" required></textarea></label>
<button class="btn primary" type="submit">Send</button>
</form>
<p class="note">${esc(company)}, ${esc(address)}.</p>`,
});

function redirectPage(path, target, heading) {
  pages.push({
    path,
    title: heading,
    description: `${heading} continues on the Strwbrry app.`,
    index: false,
    schema: null,
    body: `<h1>${esc(heading)}</h1>
<p>This marketing site does not load signup. Continue on the app.</p>
<p><a class="btn primary" href="${esc(target)}">${esc(heading)}</a></p>`,
  });
}

redirectPage("/sign-in", `${app}/sign-in`, "Sign in");
redirectPage("/sign-up", `${app}/sign-up`, "Sign up");

pages.push({
  path: "/contact/thanks",
  title: "Message",
  description: "What happens after the contact form.",
  index: false,
  schema: null,
  body: `<h1>Contact</h1>
<p>If you reached this page from the form, this static site did not store the message. Email <a href="mailto:${email}">${email}</a> and it will be read. The form does not open an account.</p>`,
});

pages.push({
  path: "/status/subscribed",
  title: "Status updates",
  description: "Status subscription confirmation.",
  index: false,
  schema: null,
  body: `<h1>Status updates</h1>
<p>This static build does not store the address you submitted. To hear about incidents, email <a href="mailto:${email}">${email}</a> with the subject "Status".</p>`,
});

function writePage(page) {
  const rel = page.path === "/" ? "index.html" : `${page.path.slice(1)}/index.html`;
  const file = join(dist, rel);
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, layout(page));
}

mkdirSync(dist, { recursive: true });
for (const page of pages) writePage(page);

const notFound = layout({
  path: "/404",
  title: "Page not found",
  description: "That page is not on the Strwbrry site.",
  index: false,
  body: `<h1>Page not found</h1><p>That URL is not on this site. The <a href="/">home page</a> lists the public pages.</p>`,
});
writeFileSync(join(dist, "404.html"), notFound);
writeFileSync(join(dist, "500.html"), layout({
  path: "/500",
  title: "Something went wrong",
  description: "The Strwbrry site could not finish this request.",
  index: false,
  body: `<h1>Something went wrong</h1><p>The page did not load. Email <a href="mailto:${email}">${email}</a> if you were trying to report a security issue.</p>`,
}));

const indexable = pages.filter((p) => p.index);
const urls = indexable
  .map((p) => {
    const loc = site + (p.path === "/" ? "/" : p.path);
    return `  <url><loc>${loc}</loc><lastmod>2026-10-18</lastmod></url>`;
  })
  .join("\n");
writeFileSync(
  join(dist, "sitemap.xml"),
  `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`,
);
writeFileSync(join(dist, "robots.txt"), `User-agent: *\nAllow: /\nSitemap: ${site}/sitemap.xml\n`);
writeFileSync(join(dist, "logo.svg"), logo);
writeFileSync(join(dist, "og.svg"), readFileSync(join(repo, "brand/og.svg")));
writeFileSync(
  join(dist, "help.js"),
  `const q = document.querySelector("#help-q");
const items = [...document.querySelectorAll("[data-article]")];
const empty = document.querySelector("#help-empty");
q?.addEventListener("input", () => {
  const parts = q.value.toLowerCase().split(",").map((s) => s.trim()).filter(Boolean);
  let shown = 0;
  for (const el of items) {
    const text = (el.textContent ?? "").toLowerCase();
    const ok = parts.length === 0 || parts.some((part) => text.includes(part));
    el.hidden = !ok;
    if (ok) shown += 1;
  }
  if (empty) empty.hidden = shown !== 0;
});
`,
);

const expected = [
  "/",
  "/pricing",
  "/product",
  "/security",
  "/brokers",
  "/use-cases/paper",
  "/use-cases/research",
  "/help",
  ...helpArticles.map((a) => a.path),
  "/changelog",
  "/legal/terms",
  "/legal/privacy",
  "/legal/risk-disclosure",
  "/legal/cookies",
  "/status",
  "/about",
  "/contact",
];
const missing = expected.filter((p) => !pages.some((page) => page.path === p && page.index));
if (missing.length) {
  console.error(missing);
  process.exit(1);
}
console.log(`wrote ${pages.length} pages to ${dist}`);
