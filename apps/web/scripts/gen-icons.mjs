// Rasterizes public/icon.svg into the PWA icons (UI-044): 192/512 "any", a 512 maskable icon with the
// mark inside the 80 % safe zone, the 180 px Apple touch icon, and a 96 px monochrome badge.
// Run: pnpm --filter @strawberry/web exec node scripts/gen-icons.mjs (needs Playwright's Chromium).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const root = fileURLToPath(new URL("..", import.meta.url));
const svg = readFileSync(`${root}public/icon.svg`, "utf8");
const mark = svg.replace(/^<svg[^>]*>/, "").replace(/<\/svg>\s*$/, "").replace(/<rect[^>]*\/>/, "");
const bg = "#1e1e1e";

const icons = [
  { file: "icon-192.png", size: 192, html: svg },
  { file: "icon-512.png", size: 512, html: svg },
  { file: "apple-touch-icon.png", size: 180, html: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="${bg}"/>${mark}</svg>` },
  {
    file: "maskable-512.png",
    size: 512,
    html: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="${bg}"/><g transform="translate(9.6 9.6) scale(0.7)">${mark}</g></svg>`,
  },
  {
    file: "badge-96.png",
    size: 96,
    html: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><g fill="#fff">${mark.replace(/fill="[^"]*"/g, "")}</g></svg>`,
  },
];

const browser = await chromium.launch();
const page = await browser.newPage({ deviceScaleFactor: 1 });
for (const i of icons) {
  await page.setViewportSize({ width: i.size, height: i.size });
  await page.setContent(`<html><body style="margin:0;background:transparent">${i.html.replace("<svg ", `<svg width="${i.size}" height="${i.size}" `)}</body></html>`);
  await page.screenshot({ path: `${root}public/icons/${i.file}`, omitBackground: true, clip: { x: 0, y: 0, width: i.size, height: i.size } });
  console.log("wrote", i.file);
}
await browser.close();
