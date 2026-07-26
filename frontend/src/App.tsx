import { QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { RunPanel } from "./components/canvas/RunPanel";
import { Conversation } from "./components/chat/Conversation";
import { HealthIndicator } from "./components/HealthIndicator";
import { AppShell } from "./components/layout/AppShell";
import { ProjectTree } from "./components/projects/ProjectTree";
import { createQueryClient } from "./lib/queryClient";
import { useSelectionStore } from "./lib/store";

export default function App() {
  const [queryClient] = useState(createQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      <Workbench />
    </QueryClientProvider>
  );
}

function Workbench() {
  const selectedChatId = useSelectionStore((state) => state.selectedChatId);
  const selectedProjectId = useSelectionStore((state) => state.selectedProjectId);

  return (
    <AppShell
      projects={<ProjectTree />}
      canvas={
        selectedChatId && selectedProjectId ? (
          <RunPanel
            key={selectedChatId}
            chatId={selectedChatId}
            projectId={selectedProjectId}
          />
        ) : undefined
      }
      chat={
        selectedChatId ? (
          // Keyed on the chat so switching conversations remounts rather than
          // carrying a half-typed draft or an in-flight stream across.
          <Conversation key={selectedChatId} chatId={selectedChatId} />
        ) : undefined
      }
      status={<HealthIndicator />}
    />
  );
}
