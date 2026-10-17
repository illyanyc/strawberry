import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// The SPA is served by api-gateway in production. In development, Vite proxies API calls to a
// locally running gateway so cookies stay same-origin.
const gateway = process.env.TB_GATEWAY_URL ?? "http://localhost:8080";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: {
    outDir: "dist",
    sourcemap: false,
    // Keep every script external: the gateway's CSP forbids inline scripts.
    modulePreload: { polyfill: false },
    assetsInlineLimit: 0,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: gateway, changeOrigin: false },
      "/auth": { target: gateway, changeOrigin: false },
      "/healthz": { target: gateway, changeOrigin: false },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    css: false,
  },
});
