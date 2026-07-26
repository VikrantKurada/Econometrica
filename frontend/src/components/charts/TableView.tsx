import type { TableData } from "./marks";

function cell(value: unknown): { text: string; numeric: boolean } {
  if (value === null || value === undefined) return { text: "—", numeric: false };
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return { text: "—", numeric: false };
    const rounded = Math.abs(value) >= 1000 ? value.toFixed(2) : value.toPrecision(4);
    return { text: Number.parseFloat(rounded).toLocaleString(), numeric: true };
  }
  return { text: String(value), numeric: false };
}

/**
 * Every chart's table twin.
 *
 * Not a fallback — the relief channel. Three of the light-mode series sit below
 * 3:1 against the surface, which is allowed only where the values are readable
 * another way, and this is that way. It is also what makes a value reachable
 * without hovering, so no tooltip ever gates a number.
 */
export function TableView({ table, label }: { table: TableData; label: string }) {
  return (
    // Bounded and scrolled inside the card: a daily series is hundreds of rows,
    // and letting it set the card's height turns the canvas into a spreadsheet.
    <div className="scroll-thin max-h-72 overflow-auto">
      <table className="w-full border-collapse text-2xs" aria-label={label}>
        <thead className="sticky top-0 bg-surface-1">
          <tr>
            {table.columns.map((column) => (
              <th
                key={column}
                scope="col"
                className="border-b border-border px-2 py-1.5 text-left font-medium text-text-secondary"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, index) => (
            <tr key={index}>
              {row.map((value, column) => {
                const { text, numeric } = cell(value);
                return (
                  <td
                    key={column}
                    className={`border-b border-border/60 px-2 py-1 ${
                      numeric ? "tabular text-right" : "text-left"
                    }`}
                  >
                    {text}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
