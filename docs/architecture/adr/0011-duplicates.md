# ADR-0011: One front; the pricing lab stays in JavaScript

- Status: Accepted, 7 November 2026

## Context

Two pieces duplicated part of the desk: a local Gradio front-end, never deployed, and `docs/lab.js`, an independent
JavaScript re-implementation of the closed forms, CRR, an American PDE, Heston by Fourier and Monte Carlo with
Andersen's QE, published on GitHub Pages and checked against the Python library by `test_widget_js.py`.

## Decision

- The Gradio front-end is removed: the desk has one front ([ADR-0024](0024-front-end-stack.md)).
- `docs/lab.js` stays as the pricing lab that computes in the browser, published under `lab/` beside the demo. Running
  `optlab.core` itself in the browser under Pyodide was measured and set aside: prices were fast enough, but the first
  load was not (30.5 MB to download, 5.6 s to start even when cached), and it would need an older NumPy and a CSP
  opened to `'wasm-unsafe-eval'` ([ux.md](../../ux.md) §5).

## Consequences

- One front to maintain. The JavaScript lab remains an independent reference, checked against the library to 1e-9 on
  the closed forms; a change to its pricers must keep `test_widget_js.py` passing.

## Alternatives considered

- Keep the Gradio front-end: no user, and a second UI to keep in step. Rejected.
- Replace the lab by Pyodide: measured, rejected (above).
