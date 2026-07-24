import * as Tooltip from "@radix-ui/react-tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions, type RenderResult } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement, ReactNode } from "react";

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      // Retries would turn every deliberate 422 into a multi-second wait.
      queries: { retry: false, staleTime: 0, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

interface RenderWithProvidersResult extends RenderResult {
  queryClient: QueryClient;
  user: ReturnType<typeof userEvent.setup>;
}

export function renderWithProviders(
  ui: ReactElement,
  options: Omit<RenderOptions, "wrapper"> & { queryClient?: QueryClient } = {},
): RenderWithProvidersResult {
  const { queryClient = createTestQueryClient(), ...renderOptions } = options;

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <Tooltip.Provider>{children}</Tooltip.Provider>
      </QueryClientProvider>
    );
  }

  return {
    queryClient,
    // Radix marks the page inert while a modal dialog is open, which
    // user-event reads as "this element cannot be clicked". The check is about
    // pointer-events CSS, not about anything a real user would hit.
    user: userEvent.setup({ pointerEventsCheck: 0 }),
    ...render(ui, { wrapper: Wrapper, ...renderOptions }),
  };
}
