# ADR-0011: Duplicates: `space/` removed, `docs/lab.js` frozen

- Status: Proposed (A0, 7 November 2026)
- Handoff §1: Doublons

## Context

`space/app.py` (373 lines) is a local Gradio front-end, never deployed, that duplicates part of the desk. `docs/lab.js`
(1,155 lines) is an independent JavaScript re-implementation of the closed forms, CRR, an American PDE, Heston Fourier
and Monte Carlo with Andersen's QE, served by GitHub Pages and checked against Python by `tests/test_widget_js.py`.

## Decision

- `space/` is deleted (A8), with the Gradio mention in `CLAUDE.md` and the README.
- `docs/lab.js` is frozen until session 2, which replaces it with the demo mode (recorded API responses, or Pyodide
  running `optlab.core` if the trial meets its bar). Its node test keeps running while it exists.

## Consequences

- One front to maintain after session 2. The JS re-implementation remains an independent reference until then.

## Alternatives considered

- Keep `space/`: no user, a second UI to keep in step. Rejected.
