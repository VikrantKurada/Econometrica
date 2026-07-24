import { ChartSpline } from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState } from "./EmptyState";
import { PaneHeader } from "./PaneHeader";

interface CanvasPaneProps {
  children?: ReactNode;
  actions?: ReactNode;
}

/**
 * Centre pane: the artifact canvas. Charts, tables and traces land here as
 * tabs. Until they exist it says so rather than showing a mock.
 */
export function CanvasPane({ children, actions }: CanvasPaneProps) {
  return (
    <section aria-label="Artifacts" className="flex h-full flex-col bg-surface-0">
      <PaneHeader title="Canvas" actions={actions} />
      <div className="scroll-thin min-h-0 flex-1 overflow-auto">
        {children ?? (
          <EmptyState
            icon={ChartSpline}
            title="No artifacts yet"
            hint="Charts, tables and computation traces from the analysis appear here."
          />
        )}
      </div>
    </section>
  );
}
