import { QueryClientProvider } from "@tanstack/react-query";
import * as Tooltip from "@radix-ui/react-tooltip";
import { useState } from "react";

import { HealthIndicator } from "./components/HealthIndicator";
import { ThemeToggle } from "./components/ThemeToggle";
import { createQueryClient } from "./lib/queryClient";

export default function App() {
  const [queryClient] = useState(createQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      <Tooltip.Provider delayDuration={400} skipDelayDuration={200}>
        <div className="flex h-full flex-col bg-surface-0 text-text-primary">
          <header className="flex h-11 shrink-0 items-center gap-3 border-b border-border bg-surface-1 px-3">
            <span className="text-sm font-semibold tracking-tight">Econometrica</span>
            <div className="ml-auto flex items-center gap-1">
              <ThemeToggle />
            </div>
          </header>

          <main className="flex flex-1 items-center justify-center">
            <p className="text-sm text-text-secondary">The workbench shell lands next.</p>
          </main>

          <footer className="flex h-6 shrink-0 items-center gap-4 border-t border-border bg-surface-1 px-3 text-2xs">
            <HealthIndicator />
          </footer>
        </div>
      </Tooltip.Provider>
    </QueryClientProvider>
  );
}
