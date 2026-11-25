# ADR-0001: A self-hosted server on one workstation

- Status: Accepted, 7 November 2026

## Context

optlab runs on one workstation: a 12-thread CPU, 24 GB of memory, an NVIDIA GPU with 8 GB (`sm_75`), Windows 11 with
WSL2. The machine stays on, with occasional restarts. The desk serves a few named people, one tenant. The project is
both a working tool and a public showcase; when the two compete, the showcase wins, without gratuitous complexity.
No third-party account and nothing paid.

## Decision

- One self-hosted instance on that machine. No high availability: a clean restart, persistent state, recovery after a
  reboot (`restart: unless-stopped`; a job a crash interrupted ends in error).
- A few named local accounts with roles ([ADR-0020](0020-accounts-roles-audit.md)), one tenant.
- Sizing written down and measured, not guessed: one GPU owned by one worker process, the VaR on a pool of five
  processes, SQLite for the operational state.

## Consequences

- Scaling out is a non-goal. Nothing in the design forbids a second machine later (a stateless api, jobs in a table),
  but nothing is built for it.
- A power cut loses at most the running jobs; the nightly backup bounds the loss to one day for the database.
- The machine's power and sleep settings belong to its owner, not to the desk.

## Alternatives considered

- A cloud VM: needs an account and money, and moves personal-use data off the machine.
- A single process: simple, but a ten-minute VaR in the HTTP process blocks the desk and dies with it.
