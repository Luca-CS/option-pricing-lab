# Security policy

optlab is a personal, non-commercial project. Its real instance runs on its owner's machine and is never exposed to
the internet; the public face is a static demo on GitHub Pages. The threat model and the controls are in
[docs/architecture/security.md](docs/architecture/security.md).

## Reporting a vulnerability

Please do not describe a security problem in public. Open an issue titled "Security report" that says only which part
it concerns (library, engine, server, workflows, images, demo), with no detail; the owner then gives you a private way
to send the report.

Useful in a report: the version or commit, what an attacker can do, and the steps to reproduce. You should get an
answer within a week; a fix, or the reason there is none, follows as soon as it is understood. Credit is given in the
release notes unless you prefer otherwise.

## Scope

In scope: the code of this repository (the library, the native engine, the server and its API, the workflows), the
images published on GHCR from it, and the static demo.

Out of scope: denial of service by volume against a local instance, findings that need an already compromised
machine, and third-party services (GitHub itself, the market data sources).

## Supported versions

The latest release and `main`. Older releases get no fix.
