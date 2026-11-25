# The desk's front: principles, spaces, bridges, design system

The front (`frontend/`, ADR-0024) is one application on API v1, served by the API on the same origin. This note is
the plan it follows. It replaces the design of the v0 page, whose rules carry over: the form is the schema, validation
is where the eye is, jobs never freeze the page, and numbers are honest.

## 1. Principles

- **No business rule in the front.** It shows, asks and formats. Term sheet forms come from the server's schema
  (`GET /structuring/schema`: sections, `visible_if`, computed fields), and so does their validation. When a screen
  needs an aggregate or a bridge the API does not serve, the answer is a new route in `optlab.server`, with its tests.
  Filtering a list the API already serves (the trades on a name) is presentation.
- **No API type written by hand.** `src/api/schema.d.ts` is generated from `api/openapi.json`. Domain answers are
  described by `optlab.server.payloads`, and `test_api_payloads.py` checks them against real answers.
- **Honest numbers.**
  - A Monte Carlo value carries its standard error, as a dimmed `±`. A value within two SE of zero is greyed and in
    italics, with a tooltip.
  - Every result names its market (snapshot, date, origin) and its method.
  - Every market object says where it comes from: observed, derived, reconstructed, synthetic, or typed by hand.
- **Units at the border.** The API speaks in fractions and the screen in percentages; units go in column headers
  (per +1 %, per vol point, per bp, per business day).
- **Jobs never freeze a page.**
  - A long computation is a job: its button becomes a progress bar with its step, its ETA and a cancel.
  - The job also shows in the tray and ends in a notification that links to its result.
  - A job's screen follows it by its id in the URL (`?job=`), so a shared link shows the same run.
- **State in the URL.** Filters, tabs, the node picked and the run shown are search parameters, so a link reopens the
  view. One catch: React Router applies a search change inside a transition. A screen that has just set something
  (a job it started, a box it ticked) holds it locally and lets the URL follow.
- **Strict CSP.** No inline script or style, no CDN, fonts and icons served by the app. Mantine's CSS variables are
  built statically (`tooling/mantine-vars.ts`), and Biome refuses the components that write `<style>`.

## 2. Spaces

Each user lands on the home of their first role. Everyone signed in may read every space except Admin, and the server
refuses what a role may not do: a button a role cannot use is not shown.

| Space | For | Screens |
|---|---|---|
| Structuring | structurer, sales | Home · Builder · Compare · Quotes, and a quote's page |
| Trading | trader | Home · Book, and a trade's page · Book risk (Greeks, vega K×T, ladder, scenarios) · P&L explained · Lifecycle · Pricing policy |
| Risk | risk manager (control) | Home · VaR, and a run's page · Stress (signed library) · Backtest · EOD runs |
| Quant | quant, validation | Home · Market explorer, and a name's page · Calibration and model risk · Vol lab · Replay lab · Script lab · Engine |
| Admin | admin | Home · Users · Jobs · Schedules (the data pipelines) · Audit · System |

Personal preferences sit in the user menu: theme, density and a colour-blind palette.

## 3. Bridges

Each bridge is a real link, tested end to end (`frontend/e2e/bridges.spec.ts`). An entity's link (`EntityLink`) opens
its page, and its "Open in…" menu lists the other places it opens.

| # | From | To |
|---|---|---|
| 1 | A quote, "Book it" | its page with the booking dialog open (`?book=1`), then the trade. Back: the trade's quote |
| 2 | A quote | its model risk (`/quant/calibration?quote=`: its model provisions, its control); its backtest (`/quant/replay?quote=`: the quote's own product struck on a past date) |
| 3 | A trade | its Greeks (`/trading/risk?trade=`), its P&L explained (`/trading/pnl?trade=`), its lifecycle, its replay |
| 4 | A risk figure (a vega node, a VaR contribution) | the trades that carry it, then the market object behind it (`/quant/market/:name?strike=&tenor=`) |
| 5 | A market object (a name's page) | the trades exposed to it and the quotes priced on it |
| 6 | The VaR day | its moves factor by factor, then the book's P&L explained that day when an EOD run covers it |
| 7 | A scenario | its P&L by trade, and the shock in words |
| 8 | A job | its result's screen; a failed one, among the desk's jobs of its kind (Admin) |
| 9 | A quote's desk lines | the pricing policy in force (Trading, read-only to whoever may not edit it) |
| 10 | An origin badge | the lake's coverage (`/quant/market#lake`); on Admin's screens, the pipelines that fill it |

## 4. Design system

- **Look.** A modern trading desk, sober. Dark by default (graphite, not black) with a light "paper" theme, fine
  borders and no heavy shadows. Amber is the brand and focus colour, used sparingly; cyan is for interaction and
  selection. Green and red are kept for signed values, never for decoration; a blue and orange palette replaces them
  for colour-blind users.
- **Type.** Inter for the interface, JetBrains Mono for figures, tickers and ids, both served locally. Figures are
  tabular, right-aligned, with thousands separators and an explicit sign.
- **Tokens** (`src/theme/tokens.css`): every colour is a `--ol-*` variable, defined for both themes and for the
  colour-blind palette. The density changes `--ol-row`.
- **Shared components**, shown in the internal gallery `/dev/gallery` (development only):
  - display: KPI tile, value ± SE, Greeks table, K×T heatmap;
  - charts: ladder, P&L waterfall, smile and term structure, distribution with VaR and ES (ECharts, on canvas);
  - flow: event timeline, schema form, job progress;
  - markers: market and origin badges, entity link.
- **States.** Loading skeletons. Empty states say what to do. Errors show the `problem+json` detail, with each
  field's message under its field. A badge flags an old snapshot.
- **Accessibility.**
  - Nothing is told by colour alone: a sign or an arrow goes with it.
  - Focus is visible and everything works from the keyboard. Links inside a sentence are underlined, and a scrolling
    table frame takes the focus.
  - Every end-to-end test runs axe-core and fails on a serious or critical violation, in both themes.
- **Layout.** Desktop first (1280 px and up), a usable tablet, and reading on a phone. Wide tables scroll inside their
  panel, never the page.
- **Performance.** The code is split by space. ECharts and the grids load on demand. The initial JavaScript stays under
  300 kB gzipped.

## 5. The public demo

The demo is the same front, built with `--mode demo` and deployed to GitHub Pages by `.github/workflows/pages.yml`.
No request leaves the browser.

- **Replayed, not computed.** `src/demo/transport.ts` answers the API client from a recorded session.
  - `npm run demo:record` (`e2e/record`) runs on a desk serving a fresh demo data folder. It runs what the stories
    show (a quote, a booking, the book's Greeks, a three-week backfill, a VaR, a fit, a replay), then walks every screen as
    each profile. The recordings are written to `frontend/demo/recorded/`.
  - A job of the story replays over a couple of seconds, so it runs as it does on the desk.
  - A request the recording does not hold is refused, read-only, with what it would have done.
- **Synthetic data, said so.** The demo's lake is made up from a fixed seed (`optlab.marketdata.synthetic`, origin
  `synthetic`), including its option chains. The banner on every screen says so, with the data's date.
- **Three profiles, three stories** (`src/demo/stories.ts`), about three minutes each:
  - the structurer quotes a Phoenix, explains its price and model risk, and finds the trade the trader booked from it;
  - the trader reads the book's Greeks down to a vega node and its surface, the P&L explained day by day, and the VaR and
    its day;
  - the quant fits the models to a chain, reads the model risk, lives a product on past closes, and opens the script
    lab and the engine.
- **The pricing lab** (`docs/lab.js`, at `lab/` on the site) computes in the browser: closed forms, CRR, the American
  PDE, Heston by Fourier, Monte Carlo and LSM.
  - Running `optlab.core` itself under Pyodide was tried in F8 and measured. Prices are well within the budget: under
    5 ms for the closed forms, 0.3 s for a Heston Monte Carlo of 20,000 paths. But the first load is not: 30.5 MB to
    download and 5.6 s to start even when cached, so about 10 s at 50 Mbps against a budget of 10 s.
  - It would also need NumPy 2.4 (the core asks for 2.5) and a CSP opened to `'wasm-unsafe-eval'`. So the lab stays
    in JavaScript, checked against the library by `test_widget_js.py`.
