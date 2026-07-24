import { useEffect, useRef, useState } from "react";

import { cn } from "../../lib/cn";

interface InlineRenameProps {
  value: string;
  /** Accessible name for the field, e.g. `Rename project Alpha`. */
  label: string;
  /** Called with the trimmed value. Not called when nothing changed. */
  onCommit: (next: string) => void;
  onCancel: () => void;
  className?: string;
}

/**
 * The name of a row, turned into an editable field in place.
 *
 * Enter commits, Escape restores the original, and clicking away commits —
 * the same contract as a file explorer, because that is what the muscle memory
 * expects. The value is trimmed but not validated here: the API is the single
 * authority on what a legal name is, so a blank commit is sent and the 422 is
 * shown rather than being quietly swallowed by a client-side rule that could
 * drift from the server's.
 */
export function InlineRename({ value, label, onCommit, onCancel, className }: InlineRenameProps) {
  const [draft, setDraft] = useState(value);
  const inputRef = useRef<HTMLInputElement>(null);
  // Enter, Escape and the blur they cause must not both fire.
  const settled = useRef(false);

  useEffect(() => {
    inputRef.current?.select();
  }, []);

  const commit = (): void => {
    if (settled.current) return;
    settled.current = true;
    const next = draft.trim();
    if (next === value.trim()) onCancel();
    else onCommit(next);
  };

  const cancel = (): void => {
    if (settled.current) return;
    settled.current = true;
    onCancel();
  };

  return (
    <input
      ref={inputRef}
      type="text"
      aria-label={label}
      value={draft}
      spellCheck={false}
      autoComplete="off"
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onDoubleClick={(event) => event.stopPropagation()}
      onClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => {
        // The tree above listens for arrows, F2 and Delete; while editing they
        // belong to the input.
        event.stopPropagation();
        if (event.key === "Enter") {
          event.preventDefault();
          commit();
        } else if (event.key === "Escape") {
          event.preventDefault();
          cancel();
        }
      }}
      autoFocus
      className={cn(
        "min-w-0 flex-1 rounded-sm border border-accent bg-surface-0 px-1 py-px",
        "text-sm text-text-primary outline-none",
        className,
      )}
    />
  );
}
