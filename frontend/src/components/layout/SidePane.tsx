import { FolderTree } from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState } from "./EmptyState";
import { PaneHeader } from "./PaneHeader";

interface SidePaneProps {
  children?: ReactNode;
  actions?: ReactNode;
}

/**
 * Left pane: the project and chat tree. A landmark in its own right, since it
 * is how the whole workspace is navigated.
 */
export function SidePane({ children, actions }: SidePaneProps) {
  return (
    <nav aria-label="Projects" className="flex h-full flex-col bg-surface-1">
      <PaneHeader title="Projects" actions={actions} />
      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
        {children ?? (
          <EmptyState
            icon={FolderTree}
            title="No projects yet"
            hint="Projects hold the chats and artifacts for one line of analysis."
          />
        )}
      </div>
    </nav>
  );
}
