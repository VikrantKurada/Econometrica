import type { RunOutcome } from "../../lib/types";
import { unvalidatedMethods } from "./artifacts";

/**
 * Where every number in this report came from.
 *
 * **Print-only by default.** On screen the canvas already shows the data-quality
 * banner and the trace; on paper neither is reachable, and an exported artifact
 * that cannot be traced back is exactly what this project exists not to produce.
 * So the printed copy carries the manifest whether or not anyone asked for it.
 *
 * The same content the ZIP export puts in `manifest.json` — the data
 * fingerprint, the tool and its version, the parameter hash, the library
 * versions — because a PDF that reproduced less than the archive would be the
 * one artifact you could not check.
 */

export interface ProvenanceProps {
  outcome: Partial<RunOutcome>;
  /** Screen too, not only print. The gallery uses this to show the block. */
  visible?: boolean;
}

interface StepProvenance {
  stepId: string;
  tool: string;
  version: string;
  fingerprint: string;
  paramsHash: string;
  libraries: string;
}

export function stepProvenance(outcome: Partial<RunOutcome>): StepProvenance[] {
  return (outcome.execution?.outcomes ?? [])
    .filter((step) => step.result)
    .map((step) => {
      const manifest = step.result!.manifest;
      return {
        stepId: step.step_id,
        tool: manifest.tool,
        version: manifest.tool_version,
        fingerprint: manifest.data_fingerprint,
        paramsHash: manifest.params_hash,
        libraries: Object.entries(manifest.library_versions ?? {})
          .map(([name, version]) => `${name} ${version}`)
          .join(", "),
      };
    });
}

export function Provenance({ outcome, visible = false }: ProvenanceProps) {
  const steps = stepProvenance(outcome);
  const quality = outcome.quality;
  const generated = unvalidatedMethods(outcome);

  return (
    <section
      aria-label="Provenance"
      // `print-only` is display:none on screen and block in print — see
      // styles/print.css. The gallery passes `visible` to look at it.
      className={visible ? "flex flex-col gap-2" : "print-only flex-col gap-2"}
    >
      <h2 className="text-sm font-medium text-text-primary">Provenance</h2>

      {outcome.question && (
        <p className="text-2xs text-text-secondary">
          <span className="font-medium">Question:</span> {outcome.question}
        </p>
      )}

      {quality && (
        <p className="text-2xs text-text-secondary">
          <span className="font-medium">Data:</span> {quality.tickers.join(", ")}{" "}
          {quality.start}..{quality.end} ({quality.frequency},{" "}
          {quality.return_method} returns) · {quality.rows} rows · source{" "}
          {quality.source} · fingerprint <code>{quality.fingerprint}</code>
        </p>
      )}

      {quality?.flags?.length ? (
        <ul className="flex flex-col gap-0.5 text-2xs text-text-secondary">
          {quality.flags.map((flag) => (
            <li key={flag.code}>
              <span className="font-medium">{flag.code}</span> ({flag.severity}):{" "}
              {flag.detail}
            </li>
          ))}
        </ul>
      ) : null}

      {generated.length > 0 && (
        // The `unvalidated` in the version column below says this too, but a
        // reader would have to know what it means. On paper the banner is gone
        // and the trace is unreachable, so this is the only place left to say
        // it in words.
        <p className="text-2xs text-text-secondary">
          <span className="font-medium">Unvalidated method:</span>{" "}
          {generated.map((method) => `${method.method} (${method.stepId})`).join(", ")} —
          computed by code generated for this run, with no tested implementation, no
          version and no precondition gate behind it.
        </p>
      )}

      {steps.length > 0 && (
        <table className="w-full text-left text-2xs" aria-label="Result manifests">
          <thead className="text-text-secondary">
            <tr>
              {["step", "tool", "version", "data fingerprint", "params", "libraries"].map(
                (head) => (
                  <th key={head} scope="col" className="py-1 pr-3 font-medium">
                    {head}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {steps.map((step) => (
              <tr key={step.stepId} className="border-t border-border">
                <td className="py-1 pr-3">{step.stepId}</td>
                <td className="py-1 pr-3">{step.tool}</td>
                <td className="py-1 pr-3">{step.version}</td>
                {/* Not truncated. A fingerprint you cannot compare in full is
                    not a fingerprint. */}
                <td className="py-1 pr-3 font-mono break-all">{step.fingerprint}</td>
                <td className="py-1 pr-3 font-mono break-all">{step.paramsHash}</td>
                <td className="py-1 text-text-secondary">{step.libraries}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {steps.length === 0 && (
        <p className="text-2xs text-text-secondary">
          This run produced no results, so there is nothing to reproduce.
        </p>
      )}
    </section>
  );
}
