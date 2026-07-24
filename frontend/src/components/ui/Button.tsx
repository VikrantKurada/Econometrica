import type { ButtonHTMLAttributes } from "react";

import { cn } from "../../lib/cn";

type Variant = "primary" | "secondary" | "danger" | "ghost";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-white hover:bg-accent/90",
  secondary:
    "border border-border bg-surface-1 text-text-primary hover:bg-surface-2",
  danger: "bg-negative text-white hover:bg-negative/90",
  ghost: "text-text-secondary hover:bg-surface-2 hover:text-text-primary",
};

export function Button({ variant = "secondary", className, ...props }: ButtonProps) {
  return (
    <button
      type="button"
      className={cn(
        "inline-flex h-7 items-center justify-center gap-1.5 rounded px-2.5",
        "text-xs font-medium whitespace-nowrap transition-colors duration-100",
        "disabled:pointer-events-none disabled:opacity-40",
        VARIANTS[variant],
        className,
      )}
      {...props}
    />
  );
}
