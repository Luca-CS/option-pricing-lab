# ADR-0018: GitHub Actions on hosted runners only

- Status: Proposed (A0, 7 November 2026)
- Handoff §1: CI; §4.8

## Context

The repository is public. A self-hosted runner on this PC would run the code of any pull request, including one from
a stranger, on Luca's machine. GitHub is the only external service allowed.

## Decision

- **Hosted runners only, never self-hosted.** GPU and real-lake tests run locally (`optlab check gpu`, `check full`).
- `ci.yml` (pull requests and pushes to `main`), Linux and Windows: `uv sync --locked`, `ruff check`, `ruff format
  --check`, `pyright`, `lint-imports`, `pytest -m "not slow and not gpu and not lake"` with xdist, the CPU build of
  the engine and its tests, the OpenAPI drift and the TypeScript client trial, the image build (Linux).
- `security.yml`: CodeQL (python, javascript, c-cpp), gitleaks, `pip-audit` on the lock, dependency review on pull
  requests, Trivy on the images.
- `release.yml` (tags `v*`): images to GHCR with `GITHUB_TOKEN` (`packages: write` in this workflow only), SBOM and
  provenance attestation (`actions/attest-build-provenance`), a GitHub release with the archives.
- `pages.yml`: Pages moves from "branch `/docs`" to "GitHub Actions"; it first deploys today's `docs/` unchanged.
- Hardening: actions pinned by commit SHA, `permissions: contents: read` by default, no `pull_request_target`, no
  secret in pull-request workflows, approval required for workflows from outside contributors, Dependabot (uv,
  github-actions, docker; npm in session 2).
- Repository settings (announced to Luca): `main` protected (PR required, required checks, no force push, no
  deletion), secret scanning and push protection, private vulnerability reporting, `SECURITY.md`.

## Consequences

- No pull request can reach the machine. The price: GPU regressions are caught at the end of a step on the machine,
  not on each push.
- Windows runners are slower; the 5-minute target is measured on both.

## Alternatives considered

- A self-hosted runner limited to Luca's branches: one misconfiguration away from running a fork's code. Rejected.
