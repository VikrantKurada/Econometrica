import { useContext, useEffect, useRef } from "react";

import type { ResultSet } from "../../lib/types";
import { ChartHeight } from "./height";
import type { Figure } from "./marks";
import Plotly from "./plotly";
import type { ChartSpec } from "./spec";
import { PLOT_CONFIG, readChartTheme, type ChartTheme } from "./theme";

interface PlotlyFigureProps<S extends ChartSpec> {
  spec: S;
  result: ResultSet;
  /** Module-level, so its identity is stable and the effect does not churn. */
  build: (spec: S, result: ResultSet, theme: ChartTheme) => Figure;
  height: number;
}

/**
 * The one place Plotly is spoken to.
 *
 * The theme is read from this element's own computed style at draw time, so a
 * theme change is a repaint from the same CSS custom properties the rest of the
 * UI uses — no chart subscribes to the theme store, and no component has a
 * theme code path of its own. The observer below exists because Plotly renders
 * to SVG attributes, which do not re-resolve `var()` when the tokens change.
 */
export function PlotlyFigure<S extends ChartSpec>({
  spec,
  result,
  build,
  height,
}: PlotlyFigureProps<S>) {
  const host = useRef<HTMLDivElement>(null);
  // The box wins where it has an opinion — full screen is the case that does.
  const boxed = useContext(ChartHeight);

  useEffect(() => {
    const element = host.current;
    if (!element) return;

    const draw = (): void => {
      const { data, layout } = build(spec, result, readChartTheme(element));
      void Plotly.react(element, data, layout, PLOT_CONFIG);
    };

    draw();

    const observer = new MutationObserver(draw);
    observer.observe(element.ownerDocument.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });

    return () => {
      observer.disconnect();
      Plotly.purge(element);
    };
  }, [spec, result, build, boxed]);

  return <div ref={host} data-testid="plotly-figure" style={{ height: boxed ?? height }} />;
}
