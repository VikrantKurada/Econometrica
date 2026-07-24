import { QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { HealthIndicator } from "./components/HealthIndicator";
import { AppShell } from "./components/layout/AppShell";
import { ProjectTree } from "./components/projects/ProjectTree";
import { createQueryClient } from "./lib/queryClient";

export default function App() {
  const [queryClient] = useState(createQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      <AppShell projects={<ProjectTree />} status={<HealthIndicator />} />
    </QueryClientProvider>
  );
}
