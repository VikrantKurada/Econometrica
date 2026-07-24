import { FolderTree } from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState } from "./EmptyState";
import { PaneHeader } from "./PaneHeader";

interface SidePaneProps {
  children?: ReactNode;
}

/**
 * Left pane: the project and chat tree. A landmark in its own right, since it
 * is how the whole workspace is navigated.
 *
 * Unlike the other two panes this one does not draw its own header — the tree
 * owns that row, because the "new project" control belongs next to the title
 * and belongs to the tree's state.
 */
export function SidePane({ children }: SidePaneProps) {
  return (
    <nav aria-label="Projects" className="flex h-full flex-col bg-surface-1">
      {children ?? (
        <>
          <PaneHeader title="Projects" />
          <div className="min-h-0 flex-1">
            <EmptyState
              icon={FolderTree}
              title="No projects yet"
              hint="A project holds the chats and artifacts for one line of analysis."
            />
          </div>
        </>
      )}
    </nav>
  );
}
