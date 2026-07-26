import type { ResultSet } from "../../../lib/types";
import type { StatTileChartSpec } from "../spec";

/** 1,284 · 12.9K · 4.2M — compact only where the digits stop being readable. */
export function formatScalar(value: number, precision: number): string {
  if (!Number.isFinite(value)) return "—";

  const magnitude = Math.abs(value);
  if (magnitude >= 1e9) return `${(value / 1e9).toFixed(1)}B`;
  if (magnitude >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  if (magnitude >= 1e4) return `${(value / 1e3).toFixed(1)}K`;

  return value.toLocaleString(undefined, {
    minimumFractionDigits: precision,
    maximumFractionDigits: precision,
  });
}

/**
 * One number. Sometimes the honest answer is not a chart at all.
 *
 * The value takes the font's proportional figures, not `tabular-nums`: equal-
 * width digits are for columns that align vertically, and at display size they
 * make a number look loose.
 */
export function StatTile({ spec, result }: { spec: StatTileChartSpec; result: ResultSet }) {
  const value = result.scalars[spec.scalar];

  return (
    <div className="px-1 py-2">
      <div className="flex items-baseline gap-1.5">
        <span className="text-3xl font-semibold text-text-primary">
          {value === undefined ? "—" : formatScalar(value, spec.precision)}
        </span>
        {spec.unit && <span className="text-sm text-text-secondary">{spec.unit}</span>}
      </div>
    </div>
  );
}
