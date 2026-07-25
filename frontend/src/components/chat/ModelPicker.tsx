import { useQuery } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";

import { api } from "../../lib/api";
import { cn } from "../../lib/cn";
import type { ModelInfo, ProviderStatus } from "../../lib/types";

export interface ModelSelection {
  provider: string;
  model: string;
}

interface ModelPickerProps {
  value: ModelSelection | null;
  onChange: (selection: ModelSelection) => void;
  disabled?: boolean;
}

/**
 * Provider and model, chosen per message rather than per chat — switching
 * mid-conversation is the point of a five-provider application.
 *
 * Only reachable providers are offered, and models that cannot hold a
 * conversation are filtered out: an Ollama install typically carries several
 * embedding models, and picking one produces a failure that looks like a bug
 * in the app rather than a bad choice of model.
 */
export function ModelPicker({ value, onChange, disabled }: ModelPickerProps) {
  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: api.listProviders,
    staleTime: 30_000,
  });

  const usable = (providers.data ?? []).filter((p) => p.reachable);
  const provider = value?.provider ?? usable[0]?.name ?? "";

  const models = useQuery({
    queryKey: ["providers", provider, "models"],
    queryFn: () => api.listProviderModels(provider),
    enabled: Boolean(provider),
    staleTime: 30_000,
  });

  const chatModels = (models.data ?? []).filter((m) => m.capabilities.streaming);

  if (providers.isPending) {
    return <Hint>Checking providers…</Hint>;
  }

  if (usable.length === 0) {
    return <Hint>No provider is reachable. Add an API key or start Ollama.</Hint>;
  }

  return (
    <div className="flex items-center gap-2">
      <Select
        label="Provider"
        value={provider}
        disabled={disabled}
        onChange={(next) => onChange({ provider: next, model: "" })}
        options={usable.map((p: ProviderStatus) => ({ value: p.name, label: p.label }))}
      />
      <Select
        label="Model"
        value={value?.model ?? ""}
        disabled={disabled || chatModels.length === 0}
        placeholder={models.isPending ? "Loading…" : "Select a model"}
        onChange={(next) => onChange({ provider, model: next })}
        options={chatModels.map((m: ModelInfo) => ({ value: m.id, label: m.id }))}
      />
    </div>
  );
}

function Hint({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-text-secondary">{children}</p>;
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
    <div className="relative">
      <select
        aria-label={label}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className={cn(
          "h-7 appearance-none rounded border border-border bg-surface-0 py-0 pr-6 pl-2",
          "font-mono text-xs text-text-primary",
          "focus:border-accent focus:outline-none",
          "disabled:cursor-not-allowed disabled:opacity-50",
        )}
      >
        {value === "" && <option value="">{placeholder ?? `Select ${label}`}</option>}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <ChevronDown
        className="pointer-events-none absolute top-1/2 right-1.5 size-3.5 -translate-y-1/2 text-text-secondary"
        aria-hidden
      />
    </div>
  );
}
