/**
 * The Plotly bundle, assembled from parts.
 *
 * The full distribution is ~3 MB minified — it carries 3D, geo, maps and the
 * gl2d stack, none of which any spec in `spec.ts` can ask for. Registering only
 * the four trace types the union actually needs keeps the chunk to a fraction
 * of that, and makes adding a trace a deliberate edit rather than a default.
 *
 * Four traces cover all fourteen spec types:
 *
 * - `scatter`   — line, band, panels, scatter, qq, area_stack, underwater
 * - `bar`       — bar, stem, forest
 * - `heatmap`   — heatmap
 * - `histogram` — histogram
 *
 * `stat_tile` and `table` are HTML and reach none of this.
 */

// Namespace imports because the bundle's parts are CommonJS with named
// exports; `verbatimModuleSyntax` is on and there is no default to take.
import * as bar from "plotly.js/lib/bar";
import * as Plotly from "plotly.js/lib/core";
import * as heatmap from "plotly.js/lib/heatmap";
import * as histogram from "plotly.js/lib/histogram";
import * as scatter from "plotly.js/lib/scatter";

Plotly.register([scatter, bar, heatmap, histogram]);

export default Plotly;
