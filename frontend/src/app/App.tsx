import { Center, Loader, localStorageColorSchemeManager, MantineProvider } from "@mantine/core";
import { ModalsProvider } from "@mantine/modals";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, type ReactNode, Suspense, useState } from "react";
import { createBrowserRouter, Navigate, Outlet, RouterProvider, useLocation } from "react-router";
import { useMe } from "../auth/session";
import { DemoLanding } from "../demo/Demo";
import { JobPage } from "../pages/JobPage";
import { NotFound } from "../pages/NotFound";
import { SignIn } from "../pages/SignIn";
import { AdminHome } from "../spaces/admin/AdminHome";
import { Soon } from "../spaces/common";
import { QuantHome } from "../spaces/quant/QuantHome";
import { RiskHome } from "../spaces/risk/RiskHome";
import { StructuringHome } from "../spaces/structuring/StructuringHome";
import { TradingHome } from "../spaces/trading/TradingHome";
import { cssVariablesResolver, theme } from "../theme/theme";
import { Shell } from "./Shell";
import { ENTITY_PAGES, homeOf, SPACES } from "./spaces";

const colorScheme = localStorageColorSchemeManager({ key: "optlab-color-scheme" });

/** The pages behind the sign-in: without a session, the sign-in page, then back where the user was going. */
function RequireSession() {
  const me = useMe();
  const location = useLocation();
  if (me.isPending) {
    return (
      <Center h="100vh">
        <Loader size="sm" role="status" aria-label="Loading" />
      </Center>
    );
  }
  if (!me.data) {
    return <Navigate to="/sign-in" replace state={{ from: location.pathname + location.search }} />;
  }
  return <Outlet />;
}

function Home() {
  return <Navigate to={homeOf(useMe().data)} replace />;
}

/** Admin's screens: to anyone else they do not exist (the server refuses their routes anyway). */
function AdminOnly({ children }: { children: ReactNode }) {
  const me = useMe().data;
  return me?.roles.includes("admin") ? children : <NotFound />;
}

const HOMES: Record<string, ReactNode> = {
  structuring: <StructuringHome />,
  trading: <TradingHome />,
  risk: <RiskHome />,
  quant: <QuantHome />,
  admin: <AdminHome />,
};

// each space's screens load with the space (HANDOFF_REFACTOR_2_UX.md §7: the first page stays small)
const structuring = () => import("../spaces/structuring");
const trading = () => import("../spaces/trading");
const risk = () => import("../spaces/risk");
const admin = () => import("../spaces/admin");
const quant = () => import("../spaces/quant");
const LAZY: Record<string, ReturnType<typeof lazy>> = {
  "/structuring/builder": lazy(() => structuring().then((m) => ({ default: m.Builder }))),
  "/structuring/quotes": lazy(() => structuring().then((m) => ({ default: m.QuotesPage }))),
  "/structuring/quotes/:id": lazy(() => structuring().then((m) => ({ default: m.QuotePage }))),
  "/structuring/compare": lazy(() => structuring().then((m) => ({ default: m.ComparePage }))),
  "/trading/book": lazy(() => trading().then((m) => ({ default: m.Book }))),
  "/trading/risk": lazy(() => trading().then((m) => ({ default: m.BookRisk }))),
  "/trading/pnl": lazy(() => trading().then((m) => ({ default: m.Pnl }))),
  "/trading/lifecycle": lazy(() => trading().then((m) => ({ default: m.Lifecycle }))),
  "/trading/lifecycle/:id": lazy(() => trading().then((m) => ({ default: m.Lifecycle }))),
  "/trading/costs": lazy(() => trading().then((m) => ({ default: m.Costs }))),
  "/trading/trades/:id": lazy(() => trading().then((m) => ({ default: m.TradePage }))),
  "/risk/var": lazy(() => risk().then((m) => ({ default: m.Var }))),
  "/risk/var/:id": lazy(() => risk().then((m) => ({ default: m.Var }))),
  "/risk/stress": lazy(() => risk().then((m) => ({ default: m.Stress }))),
  "/risk/backtest": lazy(() => risk().then((m) => ({ default: m.Backtest }))),
  "/risk/runs": lazy(() => risk().then((m) => ({ default: m.EodRuns }))),
  "/quant/market": lazy(() => quant().then((m) => ({ default: m.Market }))),
  "/quant/market/:name": lazy(() => quant().then((m) => ({ default: m.Market }))),
  "/quant/calibration": lazy(() => quant().then((m) => ({ default: m.Calibration }))),
  "/quant/vollab": lazy(() => quant().then((m) => ({ default: m.VolLab }))),
  "/quant/replay": lazy(() => quant().then((m) => ({ default: m.Replay }))),
  "/quant/replay/:id": lazy(() => quant().then((m) => ({ default: m.Replay }))),
  "/quant/script": lazy(() => quant().then((m) => ({ default: m.Script }))),
  "/quant/engine": lazy(() => quant().then((m) => ({ default: m.Engine }))),
  "/admin/users": lazy(() => admin().then((m) => ({ default: m.Users }))),
  "/admin/jobs": lazy(() => admin().then((m) => ({ default: m.Jobs }))),
  "/admin/schedules": lazy(() => admin().then((m) => ({ default: m.Schedules }))),
  "/admin/audit": lazy(() => admin().then((m) => ({ default: m.Audit }))),
  "/admin/system": lazy(() => admin().then((m) => ({ default: m.System }))),
};

function Lazy({ path }: { path: string }) {
  const Page = LAZY[path];
  if (!Page) return <Soon />;
  return (
    <Suspense fallback={<SpaceLoading />}>
      <Page />
    </Suspense>
  );
}

function SpaceLoading() {
  return (
    <Center h={240}>
      <Loader size="sm" role="status" aria-label="Loading the screen" />
    </Center>
  );
}

function screen(spaceId: string, path: string): ReactNode {
  const page = path === `/${spaceId}` ? HOMES[spaceId] : <Lazy path={path} />;
  return spaceId === "admin" ? <AdminOnly>{page}</AdminOnly> : page;
}

// the design system's gallery, in development only: the production build drops this branch and its chunk
const Gallery = import.meta.env.DEV ? lazy(() => import("../dev/Gallery")) : null;

// the demo build has a first page of profiles instead of the sign-in, and lives under the Pages path
const DEMO = import.meta.env.MODE === "demo";
const basename = import.meta.env.BASE_URL.replace(/\/$/, "") || undefined;

const router = createBrowserRouter(
  [
    { path: "/sign-in", element: DEMO ? <DemoLanding /> : <SignIn /> },
    ...(Gallery
      ? [
          {
            path: "/dev/gallery",
            element: (
              <Suspense>
                <Gallery />
              </Suspense>
            ),
          },
        ]
      : []),
    {
      element: <RequireSession />,
      children: [
        {
          element: <Shell />,
          children: [
            { path: "/", element: <Home /> },
            ...SPACES.flatMap((space) =>
              space.screens.map((s) => ({ path: s.path, element: screen(space.id, s.path) })),
            ),
            ...ENTITY_PAGES.map((p) => ({ path: p.path, element: <Lazy path={p.path} /> })),
            { path: "/jobs/:id", element: <JobPage /> },
            { path: "*", element: <NotFound /> },
          ],
        },
      ],
    },
  ],
  { basename },
);

export function Providers({ children, client }: { children: ReactNode; client: QueryClient }) {
  return (
    <QueryClientProvider client={client}>
      {/* no <style> at run time (page CSP): Mantine's variables and classes are built statically (ADR-0024) */}
      <MantineProvider
        theme={theme}
        cssVariablesResolver={cssVariablesResolver}
        withCssVariables={false}
        withGlobalClasses={false}
        colorSchemeManager={colorScheme}
        defaultColorScheme="dark"
      >
        <ModalsProvider>{children}</ModalsProvider>
      </MantineProvider>
    </QueryClientProvider>
  );
}

export function App() {
  // one cache for the page's life, whatever renders App again
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } } }),
  );
  return (
    <Providers client={client}>
      <RouterProvider router={router} />
    </Providers>
  );
}
