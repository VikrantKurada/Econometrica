import type { ResultSet } from "../../../lib/types";
import { chartTable } from "../figure";
import type { TableChartSpec } from "../spec";
import { TableView } from "../TableView";

/** Sometimes the table *is* the chart — a table view of a table view is one table. */
export function TableChart({ spec, result }: { spec: TableChartSpec; result: ResultSet }) {
  return <TableView table={chartTable(spec, result)} label={spec.title} />;
}
