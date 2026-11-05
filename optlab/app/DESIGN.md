# optlab desk: design of the user interface

A local web application over the whole library: price a product from its term sheet, book it, follow it through
its life, look at the market behind every number, and share a result. This document is the plan the code follows.

## 1. Constraints that shape everything

| Constraint | Consequence |
|---|---|
| The engine runs on Luca's PC (NumPy, SciPy, optional GPU), calibrations and Yahoo data are local | The browser talks to a **local Python server**; nothing is computed in the browser except formatting and charts |
| A public https page cannot call `localhost` (Chrome Private Network Access) | The **server serves the UI itself** (same origin): `python -m optlab.app` then open the printed address |
| Portable, easy to set up, no installation without approval | **Zero new dependency**: standard-library HTTP server and SQLite; the front is plain HTML, CSS and JavaScript modules, no build step, no CDN (works offline) |
| Shareable while the back stays on the PC | (1) LAN: `--host 0.0.0.0` with an access token; (2) any tunnel Luca chooses (Tailscale, Cloudflare Tunnel) pointed at the port, same token; (3) **standalone HTML reports** of a quote or a book, one file that opens anywhere without the server |
| Honest numbers | Every price shows its standard error and method, every time is measured, every result names its market snapshot |
| Yahoo data is personal-use | Real fixings stay in the local store (`data/store`, git-ignored); exported reports show prices, not the raw quotes |

## 2. Who uses it and for what

A structurer or trader, alone, on a desk-like workflow:

1. **Quote**: pick a product (preset or blank), adjust the term sheet, get the coupon (or the upfront) with the
   model the policy chose and why, the model-risk check, the call probabilities and the flows.
2. **Book**: save the quote as a trade (term sheet + strike date + quantity), see the book's value and risk on one
   shared simulation.
3. **Follow**: enter fixings as they come, see what happened (coupons paid or missed, calls, knock-ins) and what is
   next, reprice the running trades.
4. **Understand the market**: the snapshot in use, the smile, the term structure, how far the models agree.
5. **Share**: export a quote or the book as a self-contained report.

## 3. Information architecture

A left navigation rail with nine destinations, each one screen, plus a global status bar.

| Screen | Purpose | Main components |
|---|---|---|
| **Overview** | Where things stand | Market card (snapshot id, valuation date, spot, ATM vols 3M/1Y/2Y), book card (value, delta, vega, trades by status), next events (observations and payments in the next 30 days), recent quotes |
| **Pricer** | Build and quote a product | Product picker (masks and presets as cards) · term-sheet form generated from the schema, by section · quote panel · actions (book it, export, view script) |
| **Book** | Positions and risk | Trades table (sortable, filterable by status) · totals bar · per-trade detail drawer · "price the book" with risk |
| **Lifecycle** | Fixings and events | Fixings editor (paste a column, CSV import, real to model scale) · per-trade timeline of past and next events |
| **Market** | The calibration behind the prices | Snapshot list and details · smile chart per maturity · ATM term structure · model-risk table (BS, LV, Heston, SLV) |
| **Script lab** | Products outside the schema | Editor with line numbers, errors at line and column, price under a chosen model |
| **Vol lab** | Models against real quotes | Quotes and day picker · smile with bid and ask · error against the spread by model and maturity · stored runs, each one replayable |
| **Replay** | A product through its life on real closes | Preset, strike date, frequency · value and cash · events · reconciliation · P&L explained with its residual |
| **Data** | What the lake holds | Coverage by table, origin and source · universe · quality · a market at a past date, with the origin of each part |

Global: a status bar (server connection, snapshot in use, running jobs with progress), a settings sheet (precision
preset, workers, fast models, control variates, theme), keyboard shortcuts (`g o`, `g p`, `g b`, `g l`, `g m`, `?`).

## 4. Interaction design

- **The form is the schema.** Sections and fields come from `describe()` and `json_schema()`: the front never
  duplicates the business rules. Changing a choice re-asks the server for the visible form (debounced 120 ms), so
  hidden fields disappear and computed ones (the gearing) update. Values are typed in **percent** in the UI
  (60 %, 8 % p.a.) and converted to fractions at the edge: the most common input mistake of the API (a coupon of 8
  meaning 800 %) cannot happen.
- **Validation where the eye is.** Errors appear under the field, in the units on screen ("Must be between 0 % and
  300 %"), as soon as the server rejects a value. The form keeps following the rest of the sheet, so the field to
  correct stays visible; Quote is disabled, with a tooltip, until the sheet is valid.
- **Long work never freezes the page.** A quote is a job: the button turns into a progress state with elapsed time
  and a cancel action; results stream into the panel when ready. A precision switch chooses between *Quick* (20,000
  paths, seconds) and *Precise* (200,000 paths), so iterating is fast and the final number is solid.
- **Every number carries its context.** Coupon `5.12 % p.a. ± 0.05`, method (`SLV Monte Carlo, 200,000 paths`),
  the policy's reason, the control model gap in standard errors, the snapshot id, the time taken. Units are written
  next to values; tables align decimals.
- **Progressive disclosure.** The headline first (coupon or price, ± SE), then the policy and the model check, then
  the detail (flows by date, call probabilities, Greeks, the SLV mix band, the generated script) in collapsible
  blocks.
- **Safe actions.** Deleting a trade asks for confirmation and can be undone for a few seconds; nothing is sent
  anywhere; the server only listens on 127.0.0.1 unless started otherwise.
- **State survives a reload.** The form in progress and the last results are kept in the browser (localStorage);
  trades, fixings and quotes live in the server's SQLite store.

## 5. Visual design

- **Tone**: a calm professional tool, dense but readable. Neutral surfaces, one accent colour for actions, semantic
  colours only for meaning (positive, negative, warning), never for decoration.
- **Typography**: system UI font stack; tabular figures for every number; a type scale of 12 / 14 / 16 / 20 / 28 px;
  line height 1.45 for text, 1.2 for headlines.
- **Layout**: 8 px spacing grid; content max width 1440 px; the pricer uses two columns (form 5/12, results 7/12) and
  collapses to one below 1100 px; tables scroll horizontally inside their card on narrow screens.
- **Colour**: design tokens as CSS custom properties, light and dark themes following the system setting with a
  manual override; contrast at least 4.5:1 for text (WCAG AA).
- **Charts**: inline SVG drawn by a small module (no library): bars for call probabilities by date, lines for smiles
  and term structures, with axis labels, units and hover values; colours from the tokens; a table alternative for
  every chart.
- **Accessibility**: semantic HTML (nav, main, forms with labels, tables with headers), visible focus rings, full
  keyboard use, `aria-live` regions for job progress and results, no information carried by colour alone.

## 6. Architecture

```
optlab/app/
  __main__.py      python -m optlab.app [--host 127.0.0.1] [--port 8765] [--token ...] [--workers N]
  server.py        ThreadingHTTPServer: static files + JSON API, token check, gzip off, CORS off (same origin)
  api.py           routes -> library calls, JSON in and out (no HTTP inside)
  jobs.py          background jobs (thread pool): status, progress, elapsed, result, cancel flag
  repository.py    SQLite under data/store: trades (versioned JSON), fixings, quotes, runs
  serialize.py     Quote, BookResult, Greeks, events -> JSON
  report.py        standalone HTML report of a quote or the book (inline CSS and SVG)
  static/          index.html, app.css, js/ (main.js: router and shortcuts, api.js, state.js, ui.js, charts.js,
                   format.js, views/*.js)
```

API (JSON):

| Method and path | Does |
|---|---|
| `GET /api/health` | version, snapshot id, workers, GPU availability |
| `GET /api/context` | valuation date, spot, curves summary, ATM vols, last calibrated maturity |
| `GET /api/presets` | named presets with their term sheets |
| `POST /api/termsheet/form` | term sheet (partial) -> validated sheet, visible form (`describe`) or field errors |
| `POST /api/quote` | term sheet + options -> job id |
| `GET /api/jobs/{id}` | status, progress, elapsed, result or error |
| `GET/POST/PUT/DELETE /api/trades[/{id}]` | the book's trades (versions kept) |
| `POST /api/book/price` | price the book (job): positions, totals, SE, delta, vega |
| `GET/POST /api/fixings` | fixings by underlying and date, in model scale (with an optional real-scale conversion) |
| `GET /api/trades/{id}/events` | lifecycle events from the fixings |
| `GET /api/market/smile?T=` | implied vols of the surface at T |
| `GET /api/market/risk` | the model-risk table of the calibration |
| `POST /api/script/check`, `POST /api/script/price` | script lab |
| `GET /report/quote/{id}`, `GET /report/book` | standalone HTML reports |

## 7. Data lake

The historical layer under the desk, in `optlab/marketdata/`. SQLite stays the desk's operational store (short concurrent
writes from the server); the lake is read-mostly history. Portable and serverless, as the rest of the desk.

| Layer | Technology | Content |
|---|---|---|
| Raw | files as downloaded, `raw/manifest.jsonl` (URL, date, sha256) | everything, replayable |
| Curated | Parquet, `curated/<table>/[underlying=X/]year=YYYY/` | 19 normalised tables |
| Queries | DuckDB in memory, views on the Parquet | latest versions, `as_of`, point in time |
| Operational | SQLite (`desk.sqlite`) | trades, fixings, quotes, settings |

Every curated row has `obs_date`, `ingested_at`, `source`, `run_id`, `quality` and `origin`. Loads are idempotent and
bitemporal: the same content adds nothing, a changed value adds a version, and the old one stays readable with `as_of`.

**Origin goes up to the screen.** `observed` (read from a market source), `derived` (computed from observed data),
`reconstructed` (rebuilt from indices and an assumed shape, no option quote read), `synthetic` (an assumption). The three layers
of surfaces are never mixed, and the term structure beyond one year is shown as an assumption.

**Point in time.** `pit.market_at(lake, name, day, as_of)` reads only rows dated at or before `day`, and returns a
`MarketSnapshot` at spot 100 with the real level in `spot_scale`. The anti-look-ahead tests compare it with a lake whose future was
erased, on synthetic and on real data. What the erasing cannot catch is configuration estimated later: the shape prior and the Heston
dynamics, calibrated in 2026.

**Sharing.** A desk listening beyond this machine (`--host`, `--tunnel`) sets `desk.shared`: option quotes and index levels answer 403,
fits and errors stay. Results hold performances in % of the initial fixing, never a level. The data is Yahoo personal-use data and a licensed
sample that allows evaluation without redistribution: nothing of it goes into `docs/`, the reports or git.

**Location.** `OPTLAB_DATA`, or the `optlab-data` folder beside the main checkout, so that lake and store survive worktrees.

## 8. Delivery plan and status

1. Server, jobs, repository, serialization, API, with tests (no browser needed: the API is called directly). Done:
   `tests/test_app_api.py`.
2. Front shell: tokens, layout, navigation, status bar, settings, router. Done.
3. Pricer (form from the schema, quote job, results panel, book it, export). Done.
4. Book and Lifecycle (trades table, book pricing, fixings editor, events timeline). Done.
5. Market and Script lab. Done.
6. Reports, sharing (token, LAN), README section, end-to-end check in a browser. Done.

Checked in a browser on every screen: quote, book, edit, delete and undo, fixings pasted in real levels, events,
book valuation, reports, the script lab with an error, widths down to 360 px, contrast of every token pair (AA in
both themes). Found and fixed on the way: about 250 ms per API call in the browser on Windows (headers and body sent
in two writes waited for a delayed ACK; now one buffered write, no Nagle, keep-alive), a dialog whose Escape could
confirm after an earlier OK, and a form that hid the field in error.

Second round (the "tout" scope): worst-ofs everywhere (other names and correlations as settings, quotes, the
book in two independent groups, risk per underlying), a page per trade (sheet, value at today's market, events,
versions, original quote), market snapshots registered in the local store with a switch, sortable tables, CSV
import of fixings, and a public link through an SSH tunnel (`--tunnel`, localhost.run). Checked in the browser
again, end to end: a Yahoo-style CSV attached in the fixings dialog (Close column taken, empty row reported, real
levels converted), sorting by dates and numbers on the Overview, Book and Lifecycle tables. The LAN path was checked
on this PC's network address (401 without the token, 200 with it, the page sets its cookie, a quote in 1.8 s end to
end). From a second host (WSL, on its own network) the connection times out: this PC's Wi-Fi is in the Public
profile and Windows has inbound block rules for Python, so no other machine can reach the desk until the owner
changes that. The desk now reads both (read-only, `netcheck.py`) when it listens on the network and says what to
change, in the terminal and in Settings. Opening the public tunnel, which is outbound and not affected by the
firewall, was refused by the development session's permission rules (an ingress tunnel through a third party is the
owner's call), so only the reading of its address is tested: the first real `--tunnel` start is the owner's.
Automated since: the fixings parser moved to the server (`fixings.py`, pytest), and the table sorting runs under
node (`tests/test_app_js.py`).

The path that needs no incoming connection at all: the desk snapshot (`/report/desk`, Overview > Desk snapshot), the
whole desk on one standalone page produced by the engine on this PC. It was published as a private claude.ai page
from the test store (book 539.41 ± 0.34, two trades, four quotes, the SX5E inputs); its link opens on any machine
signed in to the account, and the page's Share menu can open it to anyone with the link. It is a snapshot, not a
live desk: live use from another machine still needs the LAN (firewall) or the tunnel.

The tunnel was then opened for real with the owner's approval, on the test store: public address in 1.8 s, 401
without the token and 200 with it (634 ms through the relay), the page served, and from WSL through the public
address a full quote in 2.3 s (4.960 % ± 0.161). The first start showed that the relay's welcome banner prints
other addresses (admin.localhost.run): only the announced *.lhr.life address is taken now, with a test on that
banner. The relay does not pass the token cookie, so the shared address keeps its ?token= (the page sends it on
every call).

Limits that remain, from the library rather than the page:

- Names other than SPX have no smile (flat Black-Scholes) and the correlation is an input; SLV is not extended to
  baskets, so worst-ofs are priced with local vol per name.
- A market snapshot is a calibration: a new market needs `scripts/run_calibration.py`, the desk does not
  recalibrate.
