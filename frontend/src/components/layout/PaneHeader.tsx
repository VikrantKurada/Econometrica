import type { ReactNode } from "react";

import { cn } from "../../lib/cn";

interface PaneHeaderProps {
  title: string;
  /** Right-aligned controls; kept out of the tab order of the pane body. */
  actions?: ReactNode;
  className?: string;
}

/**
 * The one-line label every pane wears. Deliberately quiet: uppercase micro-type
 * in the secondary colour, so the pane content is what the eye lands on.
 */
export function PaneHeader({ title, actions, className }: PaneHeaderProps) {
  return (
    <div
      className={cn(
        "flex h-9 shrink-0 items-center gap-1 border-b border-border px-2 pl-3",
        className,
      )}
    >
      <h2 className="text-2xs font-medium tracking-[0.08em] text-text-secondary uppercase">
        {title}
      </h2>
      {actions ? <div className="ml-auto flex items-center gap-0.5">{actions}</div> : null}
    </div>
  );
}
