import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";

import { Button } from "./Button";

interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  destructive?: boolean;
}

/**
 * A modal that stands between the user and something irreversible. Focus is
 * trapped, Escape and the overlay dismiss it, and the confirm button is the
 * only way through.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  onConfirm,
  destructive = false,
}: ConfirmDialogProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <Dialog.Content
          className={
            "fixed top-1/2 left-1/2 z-50 w-[min(26rem,calc(100vw-2rem))] " +
            "-translate-x-1/2 -translate-y-1/2 rounded-md border border-border " +
            "bg-surface-1 p-4 shadow-lg outline-none"
          }
        >
          <Dialog.Title className="text-sm font-semibold text-text-primary">{title}</Dialog.Title>
          <Dialog.Description className="mt-1.5 text-xs leading-relaxed text-text-secondary">
            {description}
          </Dialog.Description>
          <div className="mt-4 flex justify-end gap-2">
            <Dialog.Close asChild>
              <Button variant="secondary">Cancel</Button>
            </Dialog.Close>
            <Button variant={destructive ? "danger" : "primary"} onClick={onConfirm} autoFocus>
              {confirmLabel}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
