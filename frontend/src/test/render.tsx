import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router";
import { theme } from "../theme/theme";

/** Render inside the app's providers (theme, queries, a router at `path`). */
export function renderInApp(ui: ReactElement, path = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MantineProvider theme={theme} withCssVariables={false} withGlobalClasses={false} defaultColorScheme="dark">
        <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
      </MantineProvider>
    </QueryClientProvider>,
  );
}
