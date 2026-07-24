import { useQuery } from "@tanstack/react-query";

import { api } from "../lib/api";
import { cn } from "../lib/cn";

/**
 * Proves the dev proxy and the backend are both up. Lives in the status bar,
 * quiet until something is wrong.
 */
export function HealthIndicator() {
  const { data, isPending, isError } = useQuery({
    queryKey: ["health"],
    queryFn: () => api.health(),
    refetchInterval: 30_000,
    retry: 1,
  });

  const state = isPending ? "pending" : isError ? "error" : "ok";
  const text =
    state === "pending" ? "connecting" : state === "error" ? "backend unreachable" : "connected";

  return (
    <div className="flex items-center gap-1.5" title={data ? `API v${data.version}` : undefined}>
      <span
        aria-hidden="true"
        className={cn(
          "size-1.5 rounded-full",
          state === "ok" && "bg-positive",
          state === "error" && "bg-negative",
          state === "pending" && "bg-text-secondary/50",
        )}
      />
      <span className="text-text-secondary">{text}</span>
      <span className="sr-only" role="status">
        Backend {text}
      </span>
    </div>
  );
}
