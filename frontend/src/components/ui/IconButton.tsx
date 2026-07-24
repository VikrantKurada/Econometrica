import * as Tooltip from "@radix-ui/react-tooltip";
import type { LucideIcon } from "lucide-react";
import type { ButtonHTMLAttributes } from "react";

import { cn } from "../../lib/cn";

type Variant = "ghost" | "danger";
type Size = "sm" | "md";

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Accessible name; also the tooltip text. Icon-only buttons must carry one. */
  label: string;
  icon: LucideIcon;
  variant?: Variant;
  size?: Size;
  /** Off for buttons inside menus or rows, where a tooltip would be noise. */
  tooltip?: boolean;
  active?: boolean;
}

const VARIANTS: Record<Variant, string> = {
  ghost: "text-text-secondary hover:text-text-primary hover:bg-surface-2",
  danger: "text-text-secondary hover:text-negative hover:bg-negative/10",
};

const SIZES: Record<Size, { button: string; icon: number }> = {
  sm: { button: "size-6", icon: 13 },
  md: { button: "size-7", icon: 15 },
};

export function IconButton({
  label,
  icon: Icon,
  variant = "ghost",
  size = "md",
  tooltip = true,
  active = false,
  className,
  ...props
}: IconButtonProps) {
  const dims = SIZES[size];

  const button = (
    <button
      type="button"
      aria-label={label}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded",
        "transition-colors duration-100",
        "disabled:pointer-events-none disabled:opacity-40",
        dims.button,
        VARIANTS[variant],
        active && "bg-surface-2 text-text-primary",
        className,
      )}
      {...props}
    >
      <Icon size={dims.icon} strokeWidth={1.75} aria-hidden="true" />
    </button>
  );

  if (!tooltip) return button;

  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>{button}</Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          side="bottom"
          sideOffset={6}
          className={cn(
            "z-50 rounded border border-border bg-surface-2 px-2 py-1",
            "text-2xs text-text-primary shadow-sm select-none",
          )}
        >
          {label}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}
