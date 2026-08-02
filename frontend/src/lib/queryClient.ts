import { QueryClient } from "@tanstack/react-query";

/**
 * One client for the app. Server state is small and local, so the defaults lean
 * towards freshness without hammering a localhost backend.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 10_000,
        refetchOnWindowFocus: false,
        retry: 1,
      },
      mutations: { retry: 0 },
    },
  });
}

export const queryKeys = {
  health: ["health"] as const,
  projects: ["projects"] as const,
  chats: (projectId: string) => ["chats", projectId] as const,
  capabilities: (chatId: string) => ["capabilities", chatId] as const,
  datasets: (projectId: string) => ["datasets", projectId] as const,
};
