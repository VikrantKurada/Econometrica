import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play } from "lucide-react";
import { useRef, useState } from "react";

import { api } from "../../lib/api";
import { streamRun, type RunPhase } from "../../lib/streamRun";
import type { ModelInfo, ProviderStatus } from "../../lib/types";
import { ArtifactCanvas } from "./ArtifactCanvas";

/** Roles that must be assigned before `POST /runs` will start anything. */
const ANALYSIS_ROLES = ["planner", "narrator"] as const;

/**
 * Runs, at last given a face.
 *
 * `POST /api/chats/{id}/runs` and `GET /api/runs/{id}` have worked since Phase
 * 4 with nothing in the app calling them, which is why that phase's gate is
 * API-level. This is the caller.
 *
 * The model assignment is written just before starting, because the backend
 * binds roles from `Project.model_assignments` and refuses a run outright when
 * a required one is missing. The validator can be given a *different* model
 * here on purpose: the design asks for review by a second opinion, and when it
 * is the same one the pipeline says so in a warning the canvas surfaces.
 */
export function RunPanel({ chatId, projectId }: { chatId: string; projectId: string }) {
  const queryClient = useQueryClient();
  const [question, setQuestion] = useState("");
  const [analysis, setAnalysis] = useState<{ provider: string; model: string } | null>(null);
  const [reviewer, setReviewer] = useState("");
  const [phases, setPhases] = useState<RunPhase[]>([]);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const abort = useRef<AbortController | null>(null);

  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: api.listProviders,
    staleTime: 30_000,
  });
  const reachable = (providers.data ?? []).filter((provider) => provider.reachable);
  const provider = analysis?.provider ?? reachable[0]?.name ?? "";

  const models = useQuery({
    queryKey: ["providers", provider, "models"],
    queryFn: () => api.listProviderModels(provider),
    enabled: Boolean(provider),
    staleTime: 30_000,
  });
  const usable = (models.data ?? []).filter((model) => model.capabilities.streaming);

  const runs = useQuery({
    queryKey: ["runs", chatId],
    queryFn: () => api.listRuns(chatId),
  });
  const latest = runs.data?.[0];

  const detail = useQuery({
    queryKey: ["run", latest?.id],
    queryFn: () => api.getRun(latest!.id),
    enabled: Boolean(latest?.id),
  });

  const rerun = useMutation({ mutationFn: api.rerunRun });

  const start = async (): Promise<void> => {
    if (!analysis?.model || !question.trim()) return;

    setRunning(true);
    setPhases([]);
    setError("");
    abort.current = new AbortController();

    try {
      const assignment = { provider: analysis.provider, model: analysis.model };
      await api.updateProject(projectId, {
        model_assignments: {
          ...Object.fromEntries(ANALYSIS_ROLES.map((role) => [role, assignment])),
          validator: { provider: analysis.provider, model: reviewer || analysis.model },
        },
      });

      await streamRun(
        chatId,
        { question: question.trim() },
        {
          signal: abort.current.signal,
          onPhase: (phase) => setPhases((current) => [...current, phase]),
          onError: setError,
          onFinished: () => setQuestion(""),
        },
      );
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setRunning(false);
      // The run is only readable from the database once the stream has ended,
      // so the refetch waits for it rather than racing the write.
      await queryClient.invalidateQueries({ queryKey: ["runs", chatId] });
    }
  };

  return (
    <div className="flex h-full flex-col">
      <form
        className="shrink-0 space-y-2 border-b border-border p-3"
        onSubmit={(event) => {
          event.preventDefault();
          void start();
        }}
      >
        <textarea
          aria-label="Question"
          rows={2}
          value={question}
          disabled={running}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Does this asset follow a random walk?"
          className="scroll-thin w-full resize-none rounded border border-border bg-surface-0 px-2 py-1.5 text-xs placeholder:text-text-secondary focus:border-accent focus:outline-none disabled:opacity-60"
        />

        <div className="flex flex-wrap items-center gap-2">
          <Select
            label="Analysis model"
            value={analysis?.model ?? ""}
            disabled={running || usable.length === 0}
            placeholder={models.isPending ? "Loading…" : "Select a model"}
            options={usable.map((model: ModelInfo) => ({ value: model.id, label: model.id }))}
            onChange={(model) => setAnalysis({ provider, model })}
          />
          <Select
            label="Validator model"
            value={reviewer}
            disabled={running || usable.length === 0}
            placeholder="Same as analysis"
            options={usable.map((model: ModelInfo) => ({ value: model.id, label: model.id }))}
            onChange={setReviewer}
          />

          <button
            type="submit"
            disabled={running || !analysis?.model || question.trim().length === 0}
            className="ml-auto flex items-center gap-1.5 rounded border border-border px-2 py-1 text-2xs text-text-secondary hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Play aria-hidden className="size-3" />
            Run analysis
          </button>
        </div>

        {reachable.length === 0 && !providers.isPending && (
          <p className="text-2xs text-text-secondary">
            No provider is reachable. Add an API key or start Ollama.
          </p>
        )}

        {error && (
          <p role="alert" className="text-2xs text-negative">
            {error}
          </p>
        )}

        {/* Kept after the run ends, and cleared when the next one starts. A
            run that fails before it is recorded leaves nothing in the
            database, so this list is the only account of what it managed. */}
        {phases.length > 0 && (
          // Capped: a revised plan re-runs every step, so the log outgrows the
          // canvas it is meant to introduce. The tail is what a reader wants.
          <ol className="scroll-thin max-h-24 space-y-0.5 overflow-y-auto text-2xs text-text-secondary">
            {phases.map((phase, index) => (
              <li key={index}>
                {phase.name.replace(/\./g, " · ")}
                {phase.detail ? ` — ${phase.detail}` : ""}
              </li>
            ))}
          </ol>
        )}
      </form>

      <div className="min-h-0 flex-1 overflow-auto">
        {detail.data ? (
          <ArtifactCanvas
            key={detail.data.id}
            run={detail.data}
            onRerun={(runId) => rerun.mutateAsync(runId)}
          />
        ) : (
          <p className="px-3 py-8 text-center text-2xs text-text-secondary">
            {runs.isPending
              ? ""
              : "No analysis has been run in this chat yet. Ask a question above and press Run analysis — the charts, findings and trace land here."}
          </p>
        )}
      </div>
    </div>
  );
}

interface SelectProps {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
  disabled?: boolean;
  placeholder?: string;
}

function Select({ label, value, options, onChange, disabled, placeholder }: SelectProps) {
  return (
    <select
      aria-label={label}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      className="h-7 rounded border border-border bg-surface-0 px-1.5 font-mono text-2xs text-text-primary focus:border-accent focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
    >
      <option value="">{placeholder ?? `Select ${label}`}</option>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

/** Kept for the type import above to stay honest about what it consumes. */
export type { ProviderStatus };
