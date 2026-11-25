# ADR-0019: Images on GHCR, a manual deploy with rollback

- Status: Accepted, 7 November 2026

## Context

The machine never accepts an inbound connection ([ADR-0002](0002-never-exposed.md)), so there is no webhook and no
push-based deploy.

## Decision

- Semantic versions; `release.yml` pushes the `api` and `worker` images to GHCR at each `vX.Y.Z` tag, with an SBOM and
  a provenance attestation, and creates a GitHub release that lists their digests. The tag must equal the packages'
  version.
- **`deploy/wsl/deploy.sh vX.Y.Z`, run by the admin on the machine**, from the repository checked out at that tag, in
  order: pull the images by the digests of the release; copy the database; stop api and worker; migrate with the new
  worker image; start both; check `GET /api/v1/ready`. If any step after the copy fails, the previous images come back
  with the copy, and the script exits with an error.
- `deploy.sh --rollback` goes back to the version before, with the database as it was before the last deploy;
  `--status` says what runs, what ran before, and the copy a rollback would restore.
- The provenance of an image can be checked with `gh attestation verify oci://ghcr.io/...@<digest>`, as the release
  notes say.

## Consequences

- Every deploy is a deliberate act with a copy of the database before it; a failed migration is undone by the
  rollback.
- The images are public: they hold code and dependencies only, never data (the `.dockerignore` is an allow-list,
  Trivy scans them).

## Alternatives considered

- Watchtower or a pull loop: automatic updates of a machine that holds personal data. Rejected.
- Building the images on the machine: no provenance, no reproducibility across machines.
