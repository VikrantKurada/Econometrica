/**
 * The chart gallery — a dev harness, not part of the app.
 *
 * The palette validator checks colour and cannot see layout, so the last step
 * of the chart procedure is to render every type and look at it. This page is
 * that step: one card per spec type over fixture data, in whichever theme is
 * stamped. Served at /gallery.html in dev; `vite build` only takes index.html,
 * so it never reaches a bundle.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { ChartCard } from "./components/charts/ChartCard";
import { CostDashboard } from "./components/telemetry/CostDashboard";
import { TraceGraph } from "./components/telemetry/TraceGraph";
import { METRICS_FIXTURE, TRACE_FIXTURE } from "./components/telemetry/fixtures";
import { ColumnMapping } from "./components/uploads/ColumnMapping";
import { UPLOAD_FIXTURE } from "./components/uploads/fixtures";
import { FIXTURE_RESULT, GALLERY } from "./components/charts/fixtures";
import { useThemeStore } from "./lib/theme";
import "./styles/index.css";

function Gallery() {
  const { resolved, toggle } = useThemeStore();

  return (
    <div className="min-h-full bg-surface-0 p-6">
      <header className="mb-4 flex items-baseline justify-between gap-4">
        <div>
          <h1 className="text-base font-semibold">Chart gallery</h1>
          <p className="mt-1 text-2xs text-text-secondary">
            Every spec type in <code>charts/spec.py</code>, over generated data. The numbers are
            synthetic — nothing here is evidence about any market.
          </p>
        </div>
        <button
          type="button"
          onClick={toggle}
          className="rounded border border-border px-2 py-1 text-2xs text-text-secondary hover:text-text-primary"
        >
          {resolved === "dark" ? "Light" : "Dark"} theme
        </button>
      </header>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {GALLERY.map((spec) => (
          <ChartCard key={spec.type} spec={spec} result={FIXTURE_RESULT} />
        ))}
      </div>

      <h2 className="mt-8 mb-2 text-base font-semibold">Run trace</h2>
      <p className="mb-3 text-2xs text-text-secondary">
        Parent links as structure, with a retry nested under the attempt it followed
        and a refusal marked. Click an agent to see what it was asked.
      </p>
      <div className="rounded-lg border border-border bg-surface-1 p-4">
        <TraceGraph steps={TRACE_FIXTURE} />
      </div>

      <h2 className="mt-8 mb-2 text-base font-semibold">Cost and latency</h2>
      <div className="rounded-lg border border-border bg-surface-1 p-4">
        <CostDashboard metrics={METRICS_FIXTURE} />
      </div>

      <h2 className="mt-8 mb-2 text-base font-semibold">Upload column mapping</h2>
      <p className="mb-3 text-2xs text-text-secondary">
        The screen a person sees before an uploaded file is stored. Every role is
        editable and the button stays disabled until the mapping is usable.
      </p>
      <div className="rounded-lg border border-border bg-surface-1 p-4">
        <ColumnMapping upload={UPLOAD_FIXTURE} onConfirm={() => undefined} />
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Gallery />
  </StrictMode>,
);
