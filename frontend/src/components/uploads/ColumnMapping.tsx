import { useMemo, useState } from "react";

import type { ColumnRole, Upload } from "../../lib/types";
import { Button } from "../ui/Button";

/**
 * The screen where a person says what an uploaded file's columns mean.
 *
 * It exists because §9 of the design puts a human between what a profiler
 * inferred and what gets stored, so the important thing about it is not that it
 * is convenient — it is that **every column is editable and nothing arrives
 * confirmed**. A screen showing a verdict and a Continue button would satisfy
 * the letter of that requirement and none of its point.
 *
 * Two things it therefore shows that a tidier design would hide: the reason
 * behind each suggestion, and — for the columns where the choice was genuinely
 * close — the fact that it *was* close. A guess whose basis is invisible is one
 * a user cannot check.
 */

const ROLES: ColumnRole[] = [
  "date",
  "ticker",
  "price",
  "return",
  "volume",
  "factor",
  "ignore",
];

/** Roles that carry an observation; a mapping needs at least one. */
const VALUE_ROLES = new Set<ColumnRole>(["price", "return", "volume", "factor"]);

export interface ColumnMappingProps {
  upload: Upload;
  onConfirm: (roles: Record<string, ColumnRole>) => void | Promise<void>;
  busy?: boolean;
  error?: string | null;
}

export function ColumnMapping({ upload, onConfirm, busy, error }: ColumnMappingProps) {
  const [roles, setRoles] = useState<Record<string, ColumnRole>>(() => ({
    ...upload.proposal.roles,
  }));

  const problems = useMemo(() => {
    const count = (predicate: (role: ColumnRole) => boolean) =>
      Object.values(roles).filter(predicate).length;

    // Mirrors `confirm_mapping` on the server. Duplicated deliberately: the
    // server is the authority and rejects the same shapes, but a user should
    // not need a round trip to learn they mapped two date columns.
    const found: string[] = [];
    const dates = count((role) => role === "date");
    if (dates === 0) found.push("Map one column as the date.");
    if (dates > 1) found.push("Only one column can be the date.");
    if (count((role) => role === "ticker") > 1) {
      found.push("Only one column can be the ticker.");
    }
    if (count((role) => VALUE_ROLES.has(role)) === 0) {
      found.push("Map at least one column as a price, return, volume or factor.");
    }
    return found;
  }, [roles]);

  const ambiguous = new Set(upload.proposal.ambiguous);

  return (
    <section aria-label="Column mapping" className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h2 className="text-sm font-medium text-text-primary">{upload.filename}</h2>
        <p className="text-2xs text-text-secondary">
          {upload.profile.rows.toLocaleString()} rows · {upload.profile.columns.length}{" "}
          columns · {upload.profile.layout} layout
          {upload.consulted_model ? " · roles suggested by a model" : ""}
        </p>
        <p className="text-2xs text-text-secondary">
          Check each column before continuing. Nothing is stored until you confirm.
        </p>
      </header>

      <div className="overflow-x-auto">
        <table className="w-full text-left text-2xs">
          <thead className="text-text-secondary">
            <tr>
              <th scope="col" className="py-1 pr-3 font-medium">
                Column
              </th>
              <th scope="col" className="py-1 pr-3 font-medium">
                Looks like
              </th>
              <th scope="col" className="py-1 pr-3 font-medium">
                Role
              </th>
              <th scope="col" className="py-1 font-medium">
                Why
              </th>
            </tr>
          </thead>
          <tbody>
            {upload.profile.columns.map((column) => (
              <tr key={column.name} className="border-t border-border">
                <td className="py-1.5 pr-3 font-mono text-text-primary">
                  {column.name}
                  {ambiguous.has(column.name) && (
                    <span
                      className="ml-2 rounded bg-surface-2 px-1 py-0.5 font-sans text-text-secondary"
                      title="More than one role fitted this column"
                    >
                      unsure
                    </span>
                  )}
                </td>
                <td className="py-1.5 pr-3 text-text-secondary">
                  <span>{column.dtype}</span>
                  {column.sample.length > 0 && (
                    <span className="ml-2 font-mono">{column.sample[0]}</span>
                  )}
                  {column.missing > 0 && (
                    <span className="ml-2">{column.missing} missing</span>
                  )}
                </td>
                <td className="py-1.5 pr-3">
                  <label className="sr-only" htmlFor={`role-${column.name}`}>
                    Role for {column.name}
                  </label>
                  <select
                    id={`role-${column.name}`}
                    className="rounded border border-border bg-surface-0 px-1.5 py-1 text-2xs text-text-primary"
                    value={roles[column.name] ?? "ignore"}
                    onChange={(event) =>
                      setRoles((current) => ({
                        ...current,
                        [column.name]: event.target.value as ColumnRole,
                      }))
                    }
                  >
                    {ROLES.map((role) => (
                      <option key={role} value={role}>
                        {role}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="py-1.5 text-text-secondary">
                  {upload.proposal.rationale[column.name] ?? ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {problems.length > 0 && (
        <ul className="flex flex-col gap-0.5 text-2xs text-negative">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}

      {error && (
        <p role="alert" className="text-2xs text-negative">
          {error}
        </p>
      )}

      <div className="flex items-center gap-3">
        <Button
          variant="primary"
          disabled={problems.length > 0 || busy}
          onClick={() => void onConfirm(roles)}
        >
          {busy ? "Confirming…" : "Confirm mapping"}
        </Button>
        {upload.confirmed && upload.observations !== null && (
          <p className="text-2xs text-text-secondary">
            {upload.observations.toLocaleString()} observations ·{" "}
            {upload.symbols.join(", ")} · {upload.fields.join(", ")}
          </p>
        )}
      </div>
    </section>
  );
}
