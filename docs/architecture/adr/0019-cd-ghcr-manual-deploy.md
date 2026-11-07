# ADR-0019: Images on GHCR, a manual deploy with rollback

- Status: Proposed (A0, 7 November 2026)
- Handoff §1: CD; §4.9

## Context

The machine must never accept an inbound connection (ADR-0002), so no webhook and no push-based deploy.

## Decision

- Semantic versions: `v1.0.0` at the end of session 1 (A9), `v2.0.0` at the end of session 2.
- `release.yml` pushes the `api` and `worker` images to GHCR at each `v*` tag, with SBOM and provenance.
- **`optlab deploy vX.Y.Z`, run by Luca on the machine**, in order: back up the databases; pull the images by digest;
  `alembic upgrade`; restart; check `GET /api/v1/ready`; on failure, restore the previous version (images and the
  pre-deploy backup). Tested on a round trip `v1.0.0-rc` -> `v1.0.0` -> rollback in A9.
- The deploy verifies the provenance attestation of the pulled images (`gh attestation verify`) before starting them.

## Consequences

- Every deploy is a deliberate act with a backup before it; a failed migration is undone by the rollback.
- Public images: they contain code and dependencies only, never data (checked by Trivy and an image content test).

## Alternatives considered

- Watchtower or a pull loop: automatic updates of a machine that holds personal data. Rejected.
- Building images on the machine: no provenance, no reproducibility across machines.
