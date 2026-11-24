# ADR-0024: The front's stack, and how it keeps the strict CSP

- Status: Accepted (F1, 24 November 2026; the choices of `HANDOFF_REFACTOR_2_UX.md` §4, agreed with Luca on 7 November)
- Handoff §4: Stack

## Context

Session 2 replaces the v0 front (`legacy/desk-static/`) with one application on API v1, served by the API on the same
origin under the page CSP of `security.md` §4: `script-src 'self'`, `style-src 'self'`, no inline script or style,
nothing from another origin. The handoff asks for the simplest stack that does the job, React (Luca knows it), free
licences, few npm dependencies, a lock file and `npm ci --ignore-scripts`.

## Decision

- **Build:** Vite 8 and TypeScript 5.9 in strict mode (`noUncheckedIndexedAccess` too), React 19, in `frontend/`.
  - TypeScript stays on 5.9: openapi-typescript 7.13 requires `typescript ^5`.
  - TypeScript 7, the native compiler, has no JavaScript API for it yet.
- **Components:** Mantine 9 (MIT), which has forms, notifications and the `Ctrl+K` palette (Spotlight).
- **Tables:** TanStack Table 8 with Mantine's styles, not Mantine React Table. MRT's latest release supports Mantine 6
  and its 2.0 beta supports Mantine 7, neither supports Mantine 9. The handoff planned this fallback.
- **Charts:** Apache ECharts (Apache 2.0), loaded on demand from F2.
- **Server data:** TanStack Query, with an `openapi-fetch` client. Its types are generated from `api/openapi.json`
  (`npm run generate`, run before `dev`, `typecheck` and `build`). They are not versioned, so they cannot drift:
  `test_openapi.py` keeps the document equal to the code, and the front must compile on it. No API type is written by
  hand. Domain payloads that the document leaves open get response models on the server as screens need them, and no
  field changes (`api.md` §10).
- **Routing:** React Router 7 in library mode. The state lives in the URL.
- **Quality:**
  - Biome for lint and format;
  - Vitest and Testing Library for unit tests;
  - Playwright with axe-core for end-to-end tests, run against the API serving the build on a demo data folder, under
    the production CSP.
- **Versions:** exact pins. Each was at least about two weeks old when chosen (the same cooldown Dependabot keeps), on
  a node of the LTS line (26).

### Keeping the CSP without `'unsafe-inline'`

Mantine writes `<style>` elements at run time in two cases, and the page CSP refuses them:
- its CSS variables and global classes, from the provider;
- the responsive style props of `Box`, and `SimpleGrid`, `Grid`, `Flex` and `AppShell`, which always write one.

The app therefore:
- runs the provider with `withCssVariables={false}` and `withGlobalClasses={false}`;
- writes the same CSS at build time, with Mantine's own functions on the app's theme (`tooling/mantine-vars.ts`, served
  as `virtual:mantine-vars.css`);
- lays out with CSS modules instead of the components that write styles. Biome refuses their import
  (`noRestrictedImports`) and `dangerouslySetInnerHTML` everywhere.

Style properties that React sets through the DOM (`element.style`) are not inline styles for the CSP, so they are
allowed: component variables, positions of popovers, ECharts' tooltips.

The build never inlines an asset as a `data:` URI (`assetsInlineLimit: 0`): fonts and scripts come from `'self'` only.

Each end-to-end test fails on any console error, and a refused style or script is one.

### Serving

- The API serves the build when `OPTLAB_FRONTEND` names it (`Settings.frontend`).
- A page path that names no file gets `index.html`, so a shared link opens the same view. A page path is one with no
  extension in its last segment, not under `/api/`.
- Assets carry their hash in their name: they are cached for a year, immutable. The page itself is revalidated.
- The `api` image builds the front in a Node stage and keeps only `dist/` (`OPTLAB_FRONTEND=/opt/frontend`).

## Consequences

- CI's `api-contract` job ("API contract (TypeScript client)", a required check of main) now compiles the front on
  the generated types, in place of `api/client-check`, which is gone; its compile-time checks moved to
  `frontend/src/api/contract.ts`. The `frontend` job lints, runs the unit tests, builds, then runs Playwright against
  `optlab serve` on fresh demo data.
- Dependabot watches `frontend/` (npm), grouped, with the 14-day cooldown.
- Mantine's whole stylesheet is in the first load: 43 kB gzip of CSS. The first page's JavaScript is 148 kB gzip,
  under the handoff's 300 kB.

## Alternatives considered

- **Mantine React Table:** not available for Mantine 9.
- **AG Grid Community:** grouping is in its paid edition.
- **A nonce per page for Mantine's `<style>` elements:** the page is static, and a nonce means the server rewrites
  every page and Mantine passes it to every style it writes. The static variables do the same with nothing at run time.
- **Committing the generated types:** a second drift check, for nothing more than generating them before each use.
- **TypeScript 7:** the generator cannot run on it yet.
