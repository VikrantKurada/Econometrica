import type { ResultSet } from "../../lib/types";
import type { ChartSpec } from "./spec";
import { AreaStackChart } from "./types/areaStack";
import { BandChart } from "./types/band";
import { BarChart } from "./types/bar";
import { ForestChart } from "./types/forest";
import { HeatmapChart } from "./types/heatmap";
import { HistogramChart } from "./types/histogram";
import { LineChart } from "./types/line";
import { PanelsChart } from "./types/panels";
import { QqChart } from "./types/qq";
import { ScatterChart } from "./types/scatter";
import { StatTile } from "./types/statTile";
import { StemChart } from "./types/stem";
import { TableChart } from "./types/table";
import { UnderwaterChart } from "./types/underwater";

/**
 * The one switch from spec to renderer.
 *
 * A Visualizer emits specs from a closed vocabulary and never writes drawing
 * code — the same containment as the tool registry — so this is where that
 * vocabulary is spent. The `never` in the default arm means a type added to the
 * union without a renderer is a compile error.
 */
export function Chart({ spec, result }: { spec: ChartSpec; result: ResultSet }) {
  switch (spec.type) {
    case "line":
      return <LineChart spec={spec} result={result} />;
    case "band":
      return <BandChart spec={spec} result={result} />;
    case "stem":
      return <StemChart spec={spec} result={result} />;
    case "panels":
      return <PanelsChart spec={spec} result={result} />;
    case "scatter":
      return <ScatterChart spec={spec} result={result} />;
    case "bar":
      return <BarChart spec={spec} result={result} />;
    case "forest":
      return <ForestChart spec={spec} result={result} />;
    case "heatmap":
      return <HeatmapChart spec={spec} result={result} />;
    case "qq":
      return <QqChart spec={spec} result={result} />;
    case "histogram":
      return <HistogramChart spec={spec} result={result} />;
    case "area_stack":
      return <AreaStackChart spec={spec} result={result} />;
    case "underwater":
      return <UnderwaterChart spec={spec} result={result} />;
    case "stat_tile":
      return <StatTile spec={spec} result={result} />;
    case "table":
      return <TableChart spec={spec} result={result} />;
    default: {
      const unreachable: never = spec;
      return unreachable;
    }
  }
}
