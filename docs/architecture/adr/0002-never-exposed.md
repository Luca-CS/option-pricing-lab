# ADR-0002: The desk is never exposed to the internet

- Status: Accepted, 7 November 2026

## Context

Safety comes first: no critical exposure of the machine. No third-party account, so no Tailscale and no Cloudflare
Tunnel. The first version of the desk offered a shared token beyond localhost and a public SSH tunnel, and ran in one
process with the machine's user rights. The repository is public and must stay clean: no secret, no data.

## Decision

- **The desk listens on `127.0.0.1` only**, by default and in every container: `127.0.0.1:` on every published port,
  because Docker bypasses the Windows and Linux firewalls and `-p 8765:8765` would listen on every interface.
- **The LAN is an option, off by default**: the `lan` compose profile starts Caddy with TLS from its internal CA in
  front of the api. It needs a Windows firewall rule limited to the Private profile and the local subnet, created by
  the machine's owner when they choose to. `GET /admin/network` reports what blocks or allows it (read-only, fixed
  arguments).
- **Never**: a port forward on the router, UPnP, a tunnel to the desk, an inbound webhook, WSL in `mirrored`
  networking mode (it would put WSL's ports on the LAN).
- **The public showcase is a static build** of the front in demo mode on GitHub Pages, without any backend.
- The repository stays public: gitleaks in pre-commit and CI, GitHub's secret scanning and push protection; data and
  the lake never in git, images or CI artefacts.

## Consequences

- Nobody outside the machine (or the LAN, when the profile is on) can reach the desk. Sharing a live desk with someone
  remote is not possible; sharing a result is (standalone HTML reports, the static demo).
- Containers add a network layer (WSL's NAT, Docker's bridge) whose bindings are checked, not assumed (`docker
  inspect`, `ss -ltnp` in WSL, `netstat` on Windows; [operations.md](../operations.md)).

## Alternatives considered

- A tunnel with accounts: still an inbound path through a third party to a process that runs code on the machine.
  Rejected.
- A VPN (WireGuard on the router or the machine): no account needed, but an inbound UDP port and key management.
  Not needed.
