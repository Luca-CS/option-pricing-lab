# ADR-0021: The v0 front during session 1

- Status: Proposed (A0, 7 November 2026)
- Handoff §1: Front pendant cette session

## Context

Answer 34: the front may break during this session. Session 2 rebuilds it on API v1 and needs the old one as a
reference for parity.

## Decision

- When the v0 server is removed (A5), `optlab/app/static/` moves to `legacy/desk-static/` with `git mv`, not served.
- Session 2 deletes it once every line of `feature-inventory.md` has its new screen or an abandonment agreed by Luca.
- Between A5 and session 2, the desk is used through the API (OpenAPI docs page on localhost) and the CLI.

## Consequences

- There is a window with no usable web front. Luca accepted it (answer 34).
- `tests/test_app_js.py` (table sorting under node) leaves with the old front; its function is in the parity list.

## Alternatives considered

- Keep the v0 front working against API v1 through an adapter: work thrown away by session 2.
