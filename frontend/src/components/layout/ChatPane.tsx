import { MessagesSquare } from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState } from "./EmptyState";
import { PaneHeader } from "./PaneHeader";

interface ChatPaneProps {
  children?: ReactNode;
  actions?: ReactNode;
  /**
   * Hand scrolling to the child. A conversation pins its composer to the
   * bottom and scrolls only the transcript, which a scroll container out here
   * would fight by scrolling the composer away too.
   */
  fill?: boolean;
}

/**
 * Right pane: the conversation. Complementary to the canvas — the canvas is
 * the subject, this is the discussion of it.
 */
export function ChatPane({ children, actions, fill = false }: ChatPaneProps) {
  return (
    <aside aria-label="Chat" className="flex h-full flex-col bg-surface-1">
      <PaneHeader title="Chat" actions={actions} />
      <div className={fill ? "min-h-0 flex-1" : "scroll-thin min-h-0 flex-1 overflow-y-auto"}>
        {children ?? (
          <EmptyState
            icon={MessagesSquare}
            title="Start a conversation"
            hint="Select a chat on the left. Analyses are run from the canvas, not from here."
          />
        )}
      </div>
    </aside>
  );
}
