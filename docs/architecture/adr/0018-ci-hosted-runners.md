# ADR-0018: GitHub Actions on hosted runners only

- Status: Accepted, 7 November 2026

## Context

The repository is public. A self-hosted runner on the desk's machine would run the code of any pull request,
including a stranger's, on that machine. GitHub is the only external service used.

## Decision

- **Hosted runners only, never self-hosted.** The GPU and the real lake are tested on the machine
  ([ADR-0017](0017-tests-and-golden-numbers.md)).
- `ci.yml` (pull requests and pushes to `main`), Linux and Windows: `uv sync --locked`, ruff, pyright, `lint-imports`,
  the test suite with xdist, the golden numbers, the CPU build of the engine and its C++ tests, the OpenAPI drift and
  the front's contract, and the front (Biome, typecheck, Vitest, build, the Playwright suites against the API and
  against the demo).
- `security.yml`: CodeQL (Python, JavaScript and TypeScript, C++), gitleaks over the whole history, `pip-audit` on the
  lock, dependency review on pull requests, Trivy on the tree. `images.yml` builds the images and scans them.
- `release.yml` (tags `v*`): images to GHCR with `GITHUB_TOKEN` (`packages: write` in this workflow only), an SBOM and
  a provenance attestation, and a GitHub release listing their digests
  ([ADR-0019](0019-cd-ghcr-manual-deploy.md)).
- `pages.yml`: the demo and the pricing lab on GitHub Pages, published by the workflow.
- Hardening: actions pinned by commit SHA, `permissions: contents: read` by default, no `pull_request_target`, no secret
  in pull-request workflows, GitHub's approval of first-time contributors' workflows, Dependabot for uv, npm,
  GitHub Actions and Docker.
- Repository settings: secret scanning, push protection and the dependency graph on; the workflows' default token is
  read-only. [SECURITY.md](../../../SECURITY.md) says how to report a vulnerability.

## Consequences

- No pull request can reach the machine. The price: GPU regressions are caught on the machine, not on each push.
- Windows runners are slower than Linux ones.

## Alternatives considered

- A self-hosted runner limited to trusted branches: one misconfiguration away from running a fork's code. Rejected.
