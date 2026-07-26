import * as Dialog from "@radix-ui/react-dialog";
import * as Tabs from "@radix-ui/react-tabs";
import { Maximize2, Pin, PinOff, X } from "lucide-react";
import { useState } from "react";

import type { RerunReport, RunDetail } from "../../lib/types";
import { ChartCard } from "../charts/ChartCard";
import { ChartHeight } from "../charts/height";
import { chartArtifacts, type ChartArtifact } from "./artifacts";
import { Diagnostics } from "./Diagnostics";
import { Findings } from "./Findings";
import { Narrative } from "./Narrative";
import { RunBanner } from "./RunBanner";
import { TraceTable } from "./TraceTable";

const NARRATIVE = "narrative";
const DIAGNOSTICS = "diagnostics";
const TRACE = "trace";

/**
 * One run, as something a person can read and interrogate.
 *
 * The layout is deliberate about what can and cannot be tabbed away. Charts,
 * the interpretation and the trace are tabs — you look at one at a time. The
 * risk flags and the findings are not: they qualify every artifact at once, so
 * a reader who never opens the right tab must still see that the prices were
 * generated and that a step refused.
 */
export function ArtifactCanvas({
  run,
  onRerun,
}: {
  run: RunDetail;
  onRerun?: (runId: string) => Promise<RerunReport>;
}) {
  const artifacts = chartArtifacts(run.outcome);
  const [pinned, setPinned] = useState<string[]>([]);
  const [fullscreen, setFullscreen] = useState<ChartArtifact | null>(null);
  const [active, setActive] = useState(artifacts[0]?.id ?? NARRATIVE);

  const togglePin = (id: string): void =>
    setPinned((current) =>
      current.includes(id) ? current.filter((pin) => pin !== id) : [...current, id],
    );

  const pinnedArtifacts = artifacts.filter((artifact) => pinned.includes(artifact.id));

  return (
    <div className="flex h-full flex-col gap-3 p-3">
      <RunBanner run={run} onRerun={onRerun} />
      <Findings outcome={run.outcome} />

      {pinnedArtifacts.length > 0 && (
        <section aria-label="Pinned" className="space-y-2">
          {pinnedArtifacts.map((artifact) => (
            <div key={artifact.id} className="relative">
              <ChartCard spec={artifact.spec} result={artifact.result} />
              <button
                type="button"
                onClick={() => togglePin(artifact.id)}
                aria-label={`Unpin ${artifact.spec.title}`}
                className="absolute right-2 top-2 rounded border border-border bg-surface-1 p-1 text-text-secondary hover:text-text-primary"
              >
                <PinOff aria-hidden className="size-3" />
              </button>
            </div>
          ))}
        </section>
      )}

      <Tabs.Root
        value={active}
        onValueChange={setActive}
        className="flex min-h-0 flex-1 flex-col"
      >
        <Tabs.List
          aria-label="Artifacts"
          className="scroll-thin flex shrink-0 gap-1 overflow-x-auto border-b border-border"
        >
          {artifacts.map((artifact) => (
            <TabTrigger key={artifact.id} value={artifact.id}>
              {artifact.spec.title}
            </TabTrigger>
          ))}
          <TabTrigger value={NARRATIVE}>Narrative</TabTrigger>
          <TabTrigger value={DIAGNOSTICS}>Diagnostics</TabTrigger>
          <TabTrigger value={TRACE}>Trace</TabTrigger>
        </Tabs.List>

        <div className="scroll-thin min-h-0 flex-1 overflow-auto pt-3">
          {artifacts.length === 0 && (
            <p className="px-1 py-6 text-center text-2xs text-text-secondary">
              This run produced no charts. A hypothesis test has nothing to draw — its finding
              is a statistic and a p-value, both under Diagnostics — and any refusal above says
              why a step produced nothing at all.
            </p>
          )}

          {artifacts.map((artifact) => (
            <Tabs.Content key={artifact.id} value={artifact.id} className="space-y-2">
              <div className="flex justify-end gap-1">
                <ArtifactAction
                  label={`${pinned.includes(artifact.id) ? "Unpin" : "Pin"} ${artifact.spec.title}`}
                  onClick={() => togglePin(artifact.id)}
                >
                  {pinned.includes(artifact.id) ? (
                    <PinOff aria-hidden className="size-3" />
                  ) : (
                    <Pin aria-hidden className="size-3" />
                  )}
                  {pinned.includes(artifact.id) ? "Unpin" : "Pin"}
                </ArtifactAction>
                <ArtifactAction
                  label={`Full screen ${artifact.spec.title}`}
                  onClick={() => setFullscreen(artifact)}
                >
                  <Maximize2 aria-hidden className="size-3" />
                  Full screen
                </ArtifactAction>
              </div>
              {/* Pinned above already; drawing it twice would mean two Plotly
                  graphs for one artifact, and two places to look. */}
              {!pinned.includes(artifact.id) && (
                <ChartCard spec={artifact.spec} result={artifact.result} />
              )}
            </Tabs.Content>
          ))}

          <Tabs.Content value={NARRATIVE}>
            <Narrative outcome={run.outcome} />
          </Tabs.Content>
          <Tabs.Content value={DIAGNOSTICS}>
            <Diagnostics outcome={run.outcome} />
          </Tabs.Content>
          <Tabs.Content value={TRACE}>
            <TraceTable steps={run.steps} />
          </Tabs.Content>
        </div>
      </Tabs.Root>

      <Dialog.Root open={fullscreen !== null} onOpenChange={(open) => !open && setFullscreen(null)}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-black/50" />
          <Dialog.Content
            aria-describedby={undefined}
            className="fixed inset-4 overflow-auto rounded-lg border border-border bg-surface-0 p-4 shadow-xl"
          >
            <Dialog.Title className="sr-only">{fullscreen?.spec.title}</Dialog.Title>
            <Dialog.Close
              aria-label="Close full screen"
              className="absolute right-3 top-3 rounded border border-border p-1 text-text-secondary hover:text-text-primary"
            >
              <X aria-hidden className="size-3.5" />
            </Dialog.Close>
            {fullscreen && (
              // The dialog is inset-4 and the card's own chrome takes about
              // 200px; the chart gets the rest. Without this the chart keeps
              // the height its type chose for a tab and leaves the bottom half
              // of the screen empty, which is not what "full screen" offers.
              <ChartHeight.Provider value={Math.max(320, window.innerHeight - 220)}>
                <ChartCard
                  // A fresh instance: the same spec drawn in a different box
                  // has to lay itself out again rather than inherit the tab's.
                  key={`${fullscreen.id}-fullscreen`}
                  spec={fullscreen.spec}
                  result={fullscreen.result}
                />
              </ChartHeight.Provider>
            )}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

function TabTrigger({ value, children }: { value: string; children: React.ReactNode }) {
  return (
    <Tabs.Trigger
      value={value}
      className="shrink-0 whitespace-nowrap border-b-2 border-transparent px-2 py-1.5 text-2xs text-text-secondary hover:text-text-primary data-[state=active]:border-accent data-[state=active]:text-text-primary"
    >
      {children}
    </Tabs.Trigger>
  );
}

function ArtifactAction({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      className="flex items-center gap-1 rounded border border-border px-2 py-0.5 text-2xs text-text-secondary hover:text-text-primary"
    >
      {children}
    </button>
  );
}
