import { useState } from "react";

import type { RunStep } from "../../lib/types";

/**
 * A run's steps as the DAG they are, rather than the list they were.
 *
 * The flat table this replaces sorted by `seq` and dropped `parent_id` on the
 * floor, which made a retry look like the next piece of work rather than a
 * second attempt at the same one — and that distinction is the whole reason
 * rejected attempts are recorded at all. Children nest under their parent with
 * a visible connector, so the shape is legible without reading ids.
 *
 * Expanding a step shows what the model was **asked** and what it **said**. §8
 * of the design asks a step to record both, and until Task 6.10 nothing did —
 * a trace that names the model but not the decision answers half the question
 * the Trace artifact exists for.
 */

const STATUS_STYLE: Record<string, string> = {
  ok: "text-text-secondary",
  refused: "text-negative font-medium",
  failed: "text-negative font-medium",
  skipped: "text-text-secondary italic",
};

export interface TraceGraphProps {
  steps: RunStep[];
}

interface Node {
  step: RunStep;
  children: Node[];
}

/** Nest each step under its parent, keeping `seq` order among siblings. */
export function buildForest(steps: RunStep[]): Node[] {
  const ordered = [...steps].sort((a, b) => a.seq - b.seq);
  const nodes = new Map<string, Node>(
    ordered.map((step) => [step.id, { step, children: [] }]),
  );
  const roots: Node[] = [];

  for (const step of ordered) {
    const node = nodes.get(step.id)!;
    const parent = step.parent_id ? nodes.get(step.parent_id) : undefined;
    // A step whose parent is missing is shown at the root rather than dropped:
    // `parent_id` is ON DELETE SET NULL, so a hole in the trace is a thing that
    // can really happen and hiding the work after it would be worse.
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  return roots;
}

export function TraceGraph({ steps }: TraceGraphProps) {
  const [open, setOpen] = useState<string | null>(null);

  if (steps.length === 0) {
    return (
      <p className="px-1 py-6 text-center text-2xs text-text-secondary">
        This run left no trace.
      </p>
    );
  }

  const total = steps.reduce((sum, step) => sum + step.cost_usd, 0);
  const tokens = steps.reduce(
    (sum, step) => sum + step.input_tokens + step.output_tokens,
    0,
  );

  return (
    <div className="scroll-thin flex flex-col gap-2 overflow-auto">
      <ol aria-label="Run trace" className="flex flex-col">
        {buildForest(steps).map((node) => (
          <TraceNode
            key={node.step.id}
            node={node}
            depth={0}
            open={open}
            onToggle={(id) => setOpen((current) => (current === id ? null : id))}
          />
        ))}
      </ol>

      <p className="border-t border-border px-2 pt-1.5 text-2xs text-text-secondary">
        {steps.length} steps · {tokens.toLocaleString()} tokens ·{" "}
        <span data-testid="trace-cost">${total.toFixed(4)}</span>
      </p>
    </div>
  );
}

function TraceNode({
  node,
  depth,
  open,
  onToggle,
}: {
  node: Node;
  depth: number;
  open: string | null;
  onToggle: (id: string) => void;
}) {
  const { step } = node;
  const expanded = open === step.id;
  // Only a tool step needs naming here: an llm step's identity is its
  // provider and model, which the column after this one already carries, and
  // printing the model in both places read as a stutter when looked at.
  const what = step.tool ?? "";
  const hasBody = Boolean(step.prompt || step.response);

  return (
    <li>
      <div
        className="flex items-baseline gap-2 py-1 text-2xs"
        // Indentation is the edge. `depth` rather than a nested list so the
        // rows stay a single column and the eye can run down the statuses.
        style={{ paddingLeft: `${depth * 14 + 4}px` }}
      >
        {depth > 0 && (
          <span aria-hidden className="text-text-secondary">
            └
          </span>
        )}
        <button
          type="button"
          disabled={!hasBody}
          onClick={() => onToggle(step.id)}
          aria-expanded={hasBody ? expanded : undefined}
          className="text-left text-text-primary hover:underline disabled:cursor-default disabled:no-underline"
        >
          {step.agent}
        </button>
        {what && <span className="text-text-secondary">{what}</span>}
        {step.attempt > 1 && (
          <span className="text-text-secondary">attempt {step.attempt}</span>
        )}
        <span className={STATUS_STYLE[step.status] ?? ""}>{step.status}</span>
        {step.provider && (
          <span className="text-text-secondary">
            {step.provider}
            {step.model ? ` · ${step.model}` : ""}
          </span>
        )}
        <span className="tabular ml-auto pr-2 text-text-secondary">
          {step.latency_ms >= 1000
            ? `${(step.latency_ms / 1000).toFixed(1)}s`
            : `${Math.round(step.latency_ms)}ms`}
        </span>
      </div>

      {expanded && (
        <div
          className="flex flex-col gap-2 pb-2"
          style={{ paddingLeft: `${depth * 14 + 18}px` }}
        >
          {step.prompt && <Body label="Asked" text={step.prompt} />}
          {step.response && <Body label="Replied" text={step.response} />}
        </div>
      )}

      {node.children.length > 0 && (
        <ol>
          {node.children.map((child) => (
            <TraceNode
              key={child.step.id}
              node={child}
              depth={depth + 1}
              open={open}
              onToggle={onToggle}
            />
          ))}
        </ol>
      )}
    </li>
  );
}

function Body({ label, text }: { label: string; text: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-2xs font-medium text-text-secondary">{label}</span>
      <pre className="scroll-thin max-h-48 overflow-auto rounded border border-border bg-surface-2 p-2 text-2xs whitespace-pre-wrap text-text-primary">
        {text}
      </pre>
    </div>
  );
}
