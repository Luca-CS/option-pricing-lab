// The demo build's extras (`vite build --mode demo`, GitHub Pages): the recorded sessions (demo/recorded/*.json,
// made by `npm run demo:record`) served beside the page as demo/<profile>.json, a 404.html that is the page itself (a
// deep link on Pages then opens the app, which routes it), and the page's CSP as a meta tag, since Pages sends none.
import { copyFileSync, existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import type { Plugin } from "vite";

const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "font-src 'self'",
  "img-src 'self' data:",
  "connect-src 'self'",
  "base-uri 'none'",
  "form-action 'none'",
  "object-src 'none'",
].join("; ");

export function demoBuild(root: string): Plugin {
  const recorded = path.join(root, "demo", "recorded");
  let outDir = "";
  return {
    name: "optlab-demo",
    apply: "build",
    configResolved(config) {
      outDir = path.resolve(config.root, config.build.outDir);
    },
    transformIndexHtml(html) {
      return html.replace("<head>", `<head>\n    <meta http-equiv="Content-Security-Policy" content="${CSP}" />`);
    },
    generateBundle() {
      if (!existsSync(recorded)) {
        this.error(`no recorded session in ${recorded}: run \`npm run demo:record\` first`);
      }
      const files = readdirSync(recorded).filter((f) => f.endsWith(".json"));
      if (files.length === 0) this.error(`no recorded session in ${recorded}`);
      for (const f of files) {
        this.emitFile({ type: "asset", fileName: `demo/${f}`, source: readFileSync(path.join(recorded, f)) });
      }
    },
    closeBundle() {
      copyFileSync(path.join(outDir, "index.html"), path.join(outDir, "404.html"));
    },
  };
}
