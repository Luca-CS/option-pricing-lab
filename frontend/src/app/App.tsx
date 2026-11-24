import { Center, Loader, localStorageColorSchemeManager, MantineProvider } from "@mantine/core";
import { ModalsProvider } from "@mantine/modals";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, type ReactNode, Suspense } from "react";
import { createBrowserRouter, Navigate, Outlet, RouterProvider, useLocation } from "react-router";
import { useMe } from "../auth/session";
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
        <Loader size="sm" aria-label="Loading" />
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

function screen(spaceId: string, path: string): ReactNode {
  const page = path === `/${spaceId}` ? HOMES[spaceId] : <Soon />;
  return spaceId === "admin" ? <AdminOnly>{page}</AdminOnly> : page;
}

// the design system's gallery, in development only: the production build drops this branch and its chunk
const Gallery = import.meta.env.DEV ? lazy(() => import("../dev/Gallery")) : null;

const router = createBrowserRouter([
  { path: "/sign-in", element: <SignIn /> },
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
          ...SPACES.flatMap((space) => space.screens.map((s) => ({ path: s.path, element: screen(space.id, s.path) }))),
          ...ENTITY_PAGES.map((p) => ({ path: p.path, element: <Soon /> })),
          { path: "/jobs/:id", element: <JobPage /> },
          { path: "*", element: <NotFound /> },
        ],
      },
    ],
  },
]);

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
  const client = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } } });
  return (
    <Providers client={client}>
      <RouterProvider router={router} />
    </Providers>
  );
}
