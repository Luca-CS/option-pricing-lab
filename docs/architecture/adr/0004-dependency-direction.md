# ADR-0004: Dependency direction, from the measured import graph

- Status: Accepted, 7 November 2026

## Context

The import graph of the single package was measured with an AST script (every `import` and `from ... import`, at
module level or inside a function, resolved to modules of `optlab`). It held **two cycles**:

- `core` <-> `native`: `price_many(engine=)` called the engine, which read the models;
- `structured` <-> `marketdata`: `marketdata` produced the pricing contexts that `structured` consumed and drove
  structured products along real history (the replay), while `structured` read reference data that happened to live in
  `marketdata` (the instrument registry, the snapshot type, the correlation repair).

## Decision

**Layers, bottom to top: `core` <- `structured` <- `marketdata` <- `risk` <- `server`**, with `native` beside them:

| Package | May import | Never imports |
|---|---|---|
| `optlab.core` | NumPy, SciPy, the standard library | any other `optlab` package |
| `optlab.structured` | `core` | `marketdata`, `risk`, `native`, `server` |
| `optlab.marketdata` | `core`, `structured` | `risk`, `native`, `server` |
| `optlab.risk` | `core`, `structured`, `marketdata` | `native`, `server` |
| `optlab.native` | `core`, `structured` (the types of a basket, lazily) | `marketdata`, `risk`, `server` |
| `optlab.server` | everything | — |

What makes it true:

1. **An engine registry in core.** `optlab.core.engines` declares the `Engine` protocol and a registry filled from the
   entry point group `optlab.engines`, loaded on first use. `optlab-native` declares its entry point. Callers ask for
   an engine by name (`numpy`, `cpu`, `gpu`, `auto`) and fall back to NumPy with the reason in
   `timings["engine_note"]`. Spawned workers receive the engine explicitly.
2. **Reference data in core**: the instrument registry (`optlab.core.instruments`), the snapshot type
   (`optlab.core.snapshot`) and Higham's correlation repair (`optlab.core.correlation`).
3. **Product formulas in structured**: the pseudo-quote formulas of local correlation (`structured.localcorr`) and the
   quick Heston prior (`structured.context`). `marketdata` keeps everything that reads the lake and imports these.
4. Paths come from `optlab.server.settings` ([ADR-0012](0012-settings.md)) as arguments; `optlab.core.config` keeps
   only `DEFAULT_SEED`.

Checks: `import-linter` in CI with a layers contract and a forbidden contract (no package but `server` imports
`optlab.native`); imports inside functions count. `test_core_alone.py` imports every module of `optlab.core` in a
fresh interpreter and finds no other `optlab` package, no pandas, DuckDB, PyArrow, requests, matplotlib or web
framework, and no new thread.

## Consequences

- `optlab.structured` installs without pandas, DuckDB or PyArrow. The contexts it prices on are built by `marketdata`
  (from the lake) or by `core.snapshot` (from a calibration file).
- `marketdata` depends on `structured`. A package that wanted market data without products would have to split
  `marketdata`; none does.
- The registry keeps the bit-for-bit invariant: which engine runs is a name; the paths it produces do not change.

## Alternatives considered

- **`structured` and `marketdata` side by side**: the market-to-context adapters would need a layer above both; `risk`
  was the only candidate, which would make the quote path depend on the risk package. Rejected.
- **`marketdata` below `structured`**: `structured` would import the lake to build its contexts, and DuckDB and
  PyArrow would leak into the product library. Rejected.
- **A protocol for every cross-package call**: more indirection than two cycles need. Rejected.
