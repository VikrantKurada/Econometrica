import type { LucideIcon } from "lucide-react";

import { cn } from "../../lib/cn";

interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  /** One sentence saying what would put something here. Never a fake preview. */
  hint?: string;
  className?: string;
}

export function EmptyState({ icon: Icon, title, hint, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex h-full flex-col items-center justify-center gap-2 px-6 text-center select-none",
        className,
      )}
    >
      <Icon size={20} strokeWidth={1.5} className="text-text-secondary/45" aria-hidden="true" />
      <p className="text-sm text-text-secondary">{title}</p>
      {hint ? <p className="max-w-[36ch] text-xs text-text-secondary/70">{hint}</p> : null}
    </div>
  );
}
