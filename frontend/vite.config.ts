/// <reference types="vitest/config" />
// The front's build and dev server (ADR-0024). In dev, Vite serves the front and proxies /api to the desk so the
// browser sees one origin (docs/architecture/api.md §9): keep changeOrigin false, the API compares Origin and Host.
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { mantineVariables } from "./tooling/mantine-vars.ts";

const api = process.env.OPTLAB_API ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react(), mantineVariables()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: { "/api": { target: api, changeOrigin: false } },
  },
  preview: { host: "127.0.0.1", port: 4173, strictPort: true },
  build: {
    target: "es2022",
    // never a data: URI: the page CSP allows fonts and scripts from 'self' only
    assetsInlineLimit: 0,
    sourcemap: false,
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["src/test/setup.ts"],
    css: false,
  },
});
