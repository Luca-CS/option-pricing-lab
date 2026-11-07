# ADR-0001: A self-hosted server on this PC

- Status: Proposed (A0, 7 November 2026)
- Handoff §1: Cible, Priorité, Utilisateurs

## Context

optlab runs on one machine: a Ryzen 5 3600 (12 threads), 24 GB of memory, an RTX 2070 Super (8 GB, `sm_75`), Windows
11 with WSL2. The machine stays on, with occasional restarts. The desk serves a few named people, one tenant. The project
is both a real tool for Luca and a portfolio showcase; when the two compete, the showcase wins, without gratuitous
complexity. No third-party account and nothing paid (answer 1).

## Decision

- One self-hosted instance on this PC. No high availability: a clean restart, persistent state, recovery after a
  reboot (`restart: unless-stopped`, jobs interrupted by a crash marked `failed`).
- A few named local accounts with roles (ADR-0020), one tenant.
- Sizing assumptions written down and measured, not guessed: one GPU owned by one worker process, VaR on 5 processes,
  SQLite for the operational state.

## Consequences

- Scaling out is a non-goal. Nothing in the design forbids a second machine later (stateless api, jobs in a table),
  but nothing is built for it.
- A power cut loses at most the running jobs; the nightly backup bounds the data loss to one day for the database.
- Luca handles the machine's power and sleep settings himself (handoff §8).

## Alternatives considered

- A cloud VM: needs an account and money (excluded by answer 1), and moves personal-use data off the PC.
- A single process (v0): simple, but a VaR of 10 minutes in the HTTP process blocks the desk and dies with it.
