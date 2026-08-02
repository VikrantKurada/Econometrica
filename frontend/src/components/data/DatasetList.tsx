import { Database } from "lucide-react";

import type { Dataset } from "../../lib/types";
import { EmptyState } from "../layout/EmptyState";

/**
 * A project's stored datasets, or an empty hint. Presentational: it is handed
 * its data and renders it, so `ProjectData` stays about the flow.
 */
export function DatasetList({ datasets }: { datasets: Dataset[] }) {
  if (datasets.length === 0) {
    return (
      <EmptyState
        icon={Database}
        title="No data yet"
        hint="Upload a CSV or Excel file to analyse it in a chat."
      />
    );
  }

  return (
    <ul aria-label="Datasets" className="flex flex-col gap-2">
      {datasets.map((dataset) => (
        <li key={dataset.id} className="rounded border border-border bg-surface-1 p-3">
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-sm font-medium text-text-primary">{dataset.name}</span>
            <span className="text-2xs text-text-secondary">
              {new Date(dataset.created_at).toLocaleDateString()}
            </span>
          </div>
          <p className="mt-1 text-2xs text-text-secondary">
            {dataset.rows.toLocaleString()} rows · {dataset.source_label}
            {dataset.symbols.length > 0 ? ` · ${dataset.symbols.join(", ")}` : ""}
          </p>
        </li>
      ))}
    </ul>
  );
}
