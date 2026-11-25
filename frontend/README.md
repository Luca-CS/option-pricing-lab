# The desk's front

One application on API v1, served by the API on the same origin (ADR-0024). Node 26 LTS.

```bash
npm ci --ignore-scripts        # the lock file, no install scripts
npm run dev                    # Vite on 127.0.0.1:5173, /api proxied to $OPTLAB_API (default the desk on 8765)
npm run typecheck              # generates src/api/schema.d.ts from ../api/openapi.json, then tsc
npm run lint                   # Biome (lint and format); npm run format writes the fixes
npm test                       # Vitest: formatting, the client, the components
npm run build                  # dist/, served by `OPTLAB_FRONTEND=dist optlab serve`
npm run e2e                    # Playwright against the API serving dist/ on E2E_DATA (see playwright.config.ts)
npm run build:demo             # the public demo: dist-demo/, under /option-pricing-lab/, from demo/recorded/
npm run e2e:demo               # the demo's own tests, on Vite's preview of dist-demo/ (no desk behind it)
npm run demo:record            # records demo/recorded/ on a fresh demo folder (E2E_DATA, E2E_PASSWORD)
```

The design system is on `/dev/gallery` in development (`npm run dev`), fed with numbers the API gave on the demo data.
Principles, spaces, bridges and the design system are in [docs/ux.md](../docs/ux.md).

## The public demo

The same front, built with `--mode demo`, is the GitHub Pages site (`.github/workflows/pages.yml`, docs/ux.md §5):
three profiles (structurer, trader, quant), each with a guided story, replaying sessions recorded on a desk. Nothing
leaves the browser: `src/demo/transport.ts` answers the client from `demo/<profile>.json`, a job of the story replays
over a couple of seconds, and a request the recording does not hold is refused as read-only.

To record again, after a change to the API or to a screen the stories open:

```bash
uv run optlab demo data --to DIR --password-stdin    # a fresh folder: synthetic numbers only
npm run build                                       # the front the recorder walks
E2E_DATA=DIR E2E_PASSWORD=... npm run demo:record   # on the fastest engine at hand (E2E_ENGINE, default auto)
```

The recorder signs in as `structurer`, `trader` and `quant` (accounts it adds), never records the login, drops the
session token, and refuses to write a file holding a local path.

## Layout

| Folder       | Holds                                                                                                                                                                                      |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `src/api`    | the generated types, the client (CSRF, problem+json as `ApiError`), compile-time checks of the contract                                                                                    |
| `src/auth`   | the session: `GET /auth/me`, login, logout, `can(me, operation)`                                                                                                                           |
| `src/lib`    | pure functions: how a number is written (`format.ts`), how a risk run's keys are read (`greeks.ts`), a scenario's shock in words (`shocks.ts`)                                             |
| `src/ui`     | the shared components: values (`Num`, `Pct`, `Est`), KPI tiles, panels, origin and market badges, entity links, job progress, timeline, Greeks table, node heatmap, empty and error states |
| `src/charts` | ECharts, loaded on demand: ladder, waterfall, curves, series, the distribution of a VaR                                                                                                    |
| `src/theme`  | the Mantine theme and the desk's tokens (`tokens.css`: graphite and paper, the colourblind palette, density)                                                                               |
| `src/pages`  | the pages outside the spaces: sign-in, a job, not found                                                                                                                                    |
| `src/spaces` | each space's screens, one chunk per space, loaded when the space is first opened                                                                                                           |
| `src/jobs`   | the jobs: their events, the tray, a screen's job (`useScreenJob`), a stored run or a fresh one (`useStoredRun`)                                                                            |
| `src/dev`    | the gallery (development only)                                                                                                                                                             |
| `src/demo`   | the public demo: the recorded transport, the stories, the first page, the banner                                                                                                           |
| `tooling`    | the build steps: Mantine's CSS variables written statically, the demo's recordings, 404 page and CSP                                                                                       |
| `e2e`        | Playwright tests (`e2e/record` is the demo's recorder, run on its own)                                                                                                                     |
| `e2e-demo`   | the demo's Playwright tests                                                                                                                                                                |
| `demo`       | the recorded sessions (`demo/recorded`, synthetic numbers only)                                                                                                                            |

## Rules

- **No business rule here.** The front shows, asks and formats. A screen that needs an aggregate the API does not
  serve gets a new route on the server, with its tests.
- **No API type by hand.** Types come from `api/openapi.json`. A payload the document leaves open gets a response
  model on the server.
- **The page CSP has no `'unsafe-inline'`.** No `<style>` or `style="…"` in markup:
  - CSS modules, and React's `style` prop (which sets properties through the DOM);
  - none of the Mantine components that write a `<style>` (Biome refuses them);
  - ECharts tooltips drawn on the canvas.

  Each e2e test fails on any console error, so a refused style fails it.
- **Numbers:**
  - monospace and tabular, with a real minus;
  - a Monte Carlo number shows its `± SE`, and one inside its noise (|v| < 2 SE) is grey and italic;
  - a signed value is never told by colour alone (sign and ▲▼);
  - levels are fractions in the API and percentages on screen, converted in `src/lib/format.ts` only.
- **Accessibility:** every control is a real button, link or input with a name; axe-core runs in the e2e tests.
