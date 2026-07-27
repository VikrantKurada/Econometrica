import { useState } from "react";

import type { ResultSet } from "../../lib/types";
import { Chart } from "./Chart";
import { chartProblem, chartTable } from "./figure";
import type { ChartSpec } from "./spec";
import { TableView } from "./TableView";

/**
 * The frame around a chart: what it is called, and the two ways to read it.
 *
 * The table view is not a fallback. Three of the light-mode series sit below
 * 3:1 against `--surface-1`, which the palette gates allow only where the
 * values are readable another way — this is that way, and it is also the
 * keyboard and screen-reader path to every number on the plot.
 *
 * The card is `bg-surface-1` on purpose: that is the surface the palette was
 * validated against, so moving it invalidates the recorded contrast run.
 */
export function ChartCard({ spec, result }: { spec: ChartSpec; result: ResultSet }) {
  const [view, setView] = useState<"chart" | "table">("chart");

  const problem = chartProblem(spec, result);
  // A table type is already the table; offering to switch to one is noise.
  const togglable = spec.type !== "table" && !problem;
  const showing = togglable ? view : "chart";

  return (
    <figure
      aria-label={spec.title}
      // The card is the unit a page break must not fall inside: half a chart is
      // not a smaller chart, it is a misleading one. See styles/print.css.
      data-print-block
      className="rounded-md border border-border bg-surface-1 p-3 text-text-primary"
    >
      <figcaption className="mb-2 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold">{spec.title}</h3>
          {spec.subtitle && (
            <p className="mt-0.5 text-2xs text-text-secondary">{spec.subtitle}</p>
          )}
        </div>

        {togglable && (
          <div role="group" aria-label="View" className="flex shrink-0 rounded border border-border">
            {([
              ["chart", "Chart"],
              ["table", "Table"],
            ] as const).map(([option, label]) => (
              <button
                key={option}
                type="button"
                aria-pressed={showing === option}
                onClick={() => setView(option)}
                className={`px-2 py-0.5 text-2xs first:rounded-l last:rounded-r ${
                  showing === option
                    ? "bg-surface-2 text-text-primary"
                    : "text-text-secondary hover:text-text-primary"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        )}
      </figcaption>

      {problem ? (
        <p role="status" className="py-6 text-center text-2xs text-text-secondary">
          This chart could not be drawn — {problem}.
        </p>
      ) : showing === "chart" ? (
        <Chart spec={spec} result={result} />
      ) : (
        <TableView table={chartTable(spec, result)} label={`${spec.title} values`} />
      )}

      {spec.caption && <p className="mt-2 text-2xs text-text-secondary">{spec.caption}</p>}
    </figure>
  );
}
