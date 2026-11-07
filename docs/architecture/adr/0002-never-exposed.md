# ADR-0002: The real instance is never exposed to the internet

- Status: Accepted (A0 checkpoint, 7 November 2026)
- Handoff §1: Exposition, Repo

## Context

"Safety above all" (Luca, 7 November): no critical exposure of the machine, no hole. No third-party account, so no
Tailscale, no Cloudflare Tunnel. v0 offered a shared token beyond localhost and a public SSH tunnel through
localhost.run (`--tunnel`); the desk ran in one process with the machine's user rights. The repository is public and
must stay clean: no secret, no data.

## Decision

- **The real instance listens on `127.0.0.1` only**, by default and in every container (`127.0.0.1:` on every
  published port: Docker bypasses the Windows and Linux firewalls, and `-p 8765:8765` would listen on every interface).
- **The LAN is an option, off by default**: the `lan` compose profile starts Caddy with TLS from its internal CA, in
  front of the api; it needs a Windows firewall rule limited to the Private profile and the local subnet, created only
  with Luca's agreement at that moment. `GET /admin/network` reports what blocks or allows it (read-only, fixed
  arguments).
- **Never**: a port forward on the router, UPnP, a tunnel to the real instance, an inbound webhook, WSL in `mirrored`
  networking mode (it would put WSL's ports on the LAN).
- **The public showcase is a static build** of the front in demo mode on GitHub Pages (session 2), without any backend.
- The `--tunnel` option, `tunnel.py` and the shared token are removed.
- The repository stays public: gitleaks in pre-commit and CI, secret scanning and push protection on GitHub, data and
  the lake never in git, images or CI artefacts.

## Consequences

- Nobody outside the PC (or the LAN, when Luca turns it on) can reach the desk. Sharing a live desk with someone
  remote is not possible; sharing a result is (standalone HTML reports, the static demo).
- Containers add a network layer (WSL NAT, Docker bridge) whose bindings must be checked, not assumed: A9 inspects
  them (`docker inspect`, `ss -ltnp` in WSL, `netstat` on Windows).

## Alternatives considered

- Keep the tunnel with accounts: still an inbound path through a third party to a process that runs code on the PC.
  Rejected (safety).
- A VPN (WireGuard on the router or the PC): no account needed, but an inbound UDP port and key management for a
  portfolio project. Not needed; can be reconsidered by a new ADR.
