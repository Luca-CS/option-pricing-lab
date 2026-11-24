import { Center, Loader, localStorageColorSchemeManager, MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, type ReactNode, Suspense } from "react";
import { createBrowserRouter, Navigate, Outlet, RouterProvider, useLocation } from "react-router";
import { useMe } from "../auth/session";
import { Home } from "../pages/Home";
import { SignIn } from "../pages/SignIn";
import { cssVariablesResolver, theme } from "../theme/theme";

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
      { path: "/", element: <Home /> },
      { path: "*", element: <Home /> },
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
        <Notifications position="bottom-right" />
        {children}
      </MantineProvider>
    </QueryClientProvider>
  );
}

export function App() {
  const client = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } });
  return (
    <Providers client={client}>
      <RouterProvider router={router} />
    </Providers>
  );
}
