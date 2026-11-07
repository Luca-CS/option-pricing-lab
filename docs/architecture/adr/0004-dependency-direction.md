# ADR-0004: Dependency direction, from the measured import graph

- Status: Accepted (A0 checkpoint, 7 November 2026) — **amends the handoff's draft** (`core <- structured`, `core <- marketdata`
  side by side) after measuring the real graph, as the handoff asked
- Handoff §1: Architecture; §3

## Context

The handoff drafted: `core` <- `native`, `core` <- `structured`, `core` <- `marketdata`, `risk` on `structured` and
`marketdata`, `server` on everything, with one known cycle (`core` <-> `native`) to break with an `Engine` protocol.
A0 measured the graph on v0 + the risk engine with an AST script (every `import` and `from ... import`, at module level
or inside a function, resolved to modules of `optlab`; `gpu.py` and `native.py` counted as `native`):

| From -> to | module-level edges | lazy edges (inside functions) |
|---|---|---|
| structured -> core | 36 | 4 |
| marketdata -> core | 23 | 4 |
| **marketdata -> structured** | **10** (all in `replay.py`) | 7 (`pit`, `names`, `derive.correl`, `quality`, `replay`) |
| **structured -> marketdata** | **3** (`schema`, `defaults`, `quanto` -> `universe`) | 4 (`context` -> `MarketSnapshot`, `localcorr` and `multiasset` -> `derive.correl`, `multiasset` -> `pit`) |
| **structured -> native** | 1 (`engine`) | 0 |
| **core -> native** | 0 | 2 (`slv`, `validation`) |
| native -> core | 5 | 1 |
| native -> structured | 0 | 3 (`fastmodels`, `localcorr`, `multiasset`: planning a `Basket`) |
| risk -> structured, marketdata, core, native | 18, 1, 6, 0 | 8, 4, 4, 1 |
| app -> everything | 40 | 9 |

So there are **two cycles**, not one: `core` <-> `native` and `structured` <-> `marketdata`. The second is not an
accident of a few lines: `marketdata` produces the pricing contexts that `structured` consumes (`pit.context_at`,
`basket_at`, `names.NameMarkets`) and drives structured products along real history (`replay`), while `structured`
reads reference data that happens to live in `marketdata` (the instrument registry, the snapshot type, the
correlation repair).

## Decision

**Layers, bottom to top: `core` <- `structured` <- `marketdata` <- `risk` <- `server`**, with `native` beside them:

| Package | May import | Never imports |
|---|---|---|
| `optlab.core` | numpy, scipy, the standard library | any other `optlab` package |
| `optlab.structured` | `core` | `marketdata`, `risk`, `native`, `server` |
| `optlab.marketdata` | `core`, `structured` | `risk`, `native`, `server` |
| `optlab.risk` | `core`, `structured`, `marketdata` | `native`, `server` |
| `optlab.native` | `core`, `structured` (types of a basket, lazily) | `marketdata`, `risk`, `server` |
| `optlab.server` | everything | — |

Moves that make it true (A2):

1. **Engine registry in core.** `optlab.core.engines` declares the `Engine` protocol (plan and run a simulation; price a
   product family) and a registry filled from the entry point group `optlab.engines`, loaded on first use.
   `optlab-native` declares its entry point. `structured.engine`, `core.slv` (`calibrate_leverage(engine=)`),
   `core.validation` and `risk` ask the registry for an engine by name (`numpy`, `cpu`, `gpu`, `auto`) and fall back
   to NumPy with the reason in `timings["engine_note"]`, as today. The process default
   (`set_default`, `$OPTLAB_ENGINE`) moves to the registry; spawned workers still receive the engine explicitly.
2. **Reference data down to core.** `marketdata/universe.py` (the instrument registry: pure dataclasses; its pandas
   table helper stays in `marketdata`) becomes `optlab.core.instruments`. The `MarketSnapshot` type (the calibrated
   market container) becomes `optlab.core.snapshot`; its file loader and the git code-version probe stay with their
   callers. `nearest_correlation` (Higham's repair, pure linear algebra) moves to `optlab.core`.
3. **Product formulas down to structured.** The pseudo-quote formulas of local correlation (`CorrelationSkew`,
   `spread_payoff`, `basket_rows`, the copula helpers they need) move from `marketdata.derive.correl` to
   `structured.localcorr`; `quick_heston` and the SLV mix constant move from `marketdata.pit` to `structured`.
   `marketdata` keeps everything that reads the lake and imports these from `structured`.
4. `optlab.data` (pandas, Yahoo chain download and cleaning) moves to `marketdata`. `optlab.config` splits:
   `DEFAULT_SEED` to `core`, paths to `server.settings` (ADR-0012), passed down as arguments.

Checks: `import-linter` in CI with a layers contract (`server`, `risk`, `marketdata`, `structured`, `core`) and a
forbidden contract (no package but `server` imports `optlab.native`), plus a test that imports `optlab.core` in a
fresh interpreter and asserts that no other `optlab` module, nor pandas, duckdb, pyarrow or FastAPI, got imported.

## Implementation (A2)

- The moves above are done, with these choices of place: the pseudo-quote formulas join `structured.localcorr`;
  `quick_heston`, its prior dynamics and the mix `MIX` join `structured.context`; `nearest_correlation` is
  `optlab.core.correlation`; the registry's table for the lake (`as_rows`) stays in `marketdata.ingest`; `optlab.data`
  is `optlab.marketdata.chains`.
- The snapshot's git probe is gone rather than moved: no caller recorded a code version (the desk and the contexts load
  with `provenance=False`), so `from_calibration_file` takes `code_version=` from whoever wants one.
- **Paths wait for `Settings` (A4).** `optlab.core.config` keeps `DEFAULT_SEED` for good and, until A4, the v0 `paths()`
  and `REPO_ROOT`: they read the environment on call (no I/O at import), and moving them now would have meant passing
  paths through every caller twice. A4 replaces them with arguments from `optlab.server.settings`.
- Checked: `lint-imports` keeps the three contracts (layers; `optlab.native` only from `server`; `native` sees nothing
  above `structured`), and a probe that adds a lazy cross-layer import breaks them; `test_core_alone.py` imports every
  module of `optlab.core` in a fresh interpreter and finds no other optlab package, no pandas, duckdb, pyarrow, requests,
  matplotlib or web framework, and no new thread.

## Consequences

- `optlab.structured` installs without pandas, duckdb or pyarrow. It loses nothing: the contexts it prices on are
  built by `marketdata` (from the lake) or by `core.snapshot` (from a calibration file).
- `marketdata` depends on `structured`. A package that wants market data without products would have to split
  `marketdata` again; no such user exists, so it is not done.
- The registry keeps the bit-for-bit invariant: which engine runs is a name; the paths it produces are unchanged.

## Alternatives considered

- **The handoff's draft (structured and marketdata side by side).** It needs the market-to-context adapters (`pit`'s
  context builders, `names`, `replay`) in a layer above both; `risk` would be the only candidate, which makes the
  desk's quote path depend on the risk package, or a new package for about 600 lines. More moves for a less natural
  boundary. Rejected, with the measurement above.
- **marketdata below structured.** `marketdata` would have to stop producing `PricingContext`s, and `structured`
  would import the lake to build them: the lake (duckdb, pyarrow) would leak into the product library. Rejected.
- **A protocol for every cross-package call.** More indirection than the two cycles need. Rejected.
