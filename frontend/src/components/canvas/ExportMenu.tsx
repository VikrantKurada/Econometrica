import { Download } from "lucide-react";

/**
 * The data exports, straight from the backend.
 *
 * Plain anchors rather than fetch-and-blob: the browser already knows how to
 * save a file the server named, and the `Content-Disposition` the API sets is
 * what gives each download a name that says which run it came from.
 *
 * Chart *images* are not here. They come from the live Plotly graph on the
 * card itself, because the renderers are TypeScript and the picture worth
 * exporting is the one on screen.
 */
const FORMATS = [
  ["markdown", "Report (.md)", "The findings, the diagnostics and the provenance"],
  ["csv", "Series (.csv)", "Every series, long-format, behind the manifest"],
  ["xlsx", "Workbook (.xlsx)", "A sheet per result, and a manifest sheet"],
  ["json", "Everything (.json)", "The whole outcome, exactly as stored"],
  ["zip", "Archive (.zip)", "All of the above, plus manifest.json"],
] as const;

export function ExportMenu({ runId }: { runId: string }) {
  return (
    <div className="space-y-1">
      <ul className="space-y-0.5">
        {FORMATS.map(([format, label, hint]) => (
          <li key={format}>
            <a
              href={`/api/runs/${runId}/export?format=${format}`}
              download
              className="flex items-baseline gap-2 rounded px-2 py-1 text-2xs hover:bg-surface-2"
            >
              <Download aria-hidden className="size-3 shrink-0 self-center text-text-secondary" />
              <span className="font-medium">{label}</span>
              <span className="text-text-secondary">{hint}</span>
            </a>
          </li>
        ))}
      </ul>
      <p className="px-2 text-2xs text-text-secondary">
        Every download carries the manifest that reproduces it — the data fingerprint, the tool
        versions and the source the prices came from.
      </p>
    </div>
  );
}
