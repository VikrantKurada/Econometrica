import { MessagesSquare } from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState } from "./EmptyState";
import { PaneHeader } from "./PaneHeader";

interface ChatPaneProps {
  children?: ReactNode;
  actions?: ReactNode;
}

/**
 * Right pane: the conversation. Complementary to the canvas — the canvas is
 * the subject, this is the discussion of it.
 */
export function ChatPane({ children, actions }: ChatPaneProps) {
  return (
    <aside aria-label="Chat" className="flex h-full flex-col bg-surface-1">
      <PaneHeader title="Chat" actions={actions} />
      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
        {children ?? (
          <EmptyState
            icon={MessagesSquare}
            title="Start a conversation"
            hint="Ask for an analysis and the results build up in the canvas."
          />
        )}
      </div>
    </aside>
  );
}
