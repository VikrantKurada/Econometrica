/**
 * Colour tokens, in a form Plotly can read.
 *
 * The app's design tokens are `oklch()`. Plotly's colour parser predates CSS
 * Color 4 and treats an oklch string as invalid — which it renders as black
 * rather than reporting, so a wrong colour would ship silently. Everything
 * handed to a trace or a layout goes through `toPlotlyColor` first.
 *
 * The conversion is the standard OKLab → linear sRGB → gamma pipeline. Doing it
 * here rather than probing the browser keeps it available under jsdom and in an
 * exported chart, and keeps it a pure function that a test can pin.
 */

const OKLCH = /^oklch\(\s*([\d.]+%?)\s+([\d.]+%?)\s+([\d.]+)(?:deg)?\s*(?:\/.*)?\)$/i;

function channel(component: number): string {
  const srgb = component <= 0.0031308 ? 12.92 * component : 1.055 * component ** (1 / 2.4) - 0.055;
  // Clamping rather than failing: an out-of-gamut token is still a colour the
  // reader should see, just the nearest one the display can show.
  const byte = Math.round(Math.min(1, Math.max(0, srgb)) * 255);
  return byte.toString(16).padStart(2, "0");
}

function number(token: string): number {
  return token.endsWith("%") ? Number.parseFloat(token) / 100 : Number.parseFloat(token);
}

/** `oklch(L C H)` as a `#rrggbb` hex. */
export function oklchToHex(lightness: number, chroma: number, hue: number): string {
  const radians = (hue * Math.PI) / 180;
  const a = chroma * Math.cos(radians);
  const b = chroma * Math.sin(radians);

  const l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (lightness - 0.0894841775 * a - 1.291485548 * b) ** 3;

  return (
    "#" +
    channel(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s) +
    channel(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s) +
    channel(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s)
  );
}

/**
 * A CSS colour token as something Plotly will parse. Hex, `rgb()` and `rgba()`
 * pass through untouched; `oklch()` is converted; anything else falls back.
 */
export function toPlotlyColor(token: string, fallback = "#000000"): string {
  const value = token.trim();
  if (!value) return fallback;

  const match = OKLCH.exec(value);
  // An oklch token that does not parse must not pass through: Plotly would
  // accept the string, fail to read it, and paint black without complaining.
  if (!match) return /^oklch\(/i.test(value) ? fallback : value;

  const [lightness, chroma, hue] = [number(match[1]), number(match[2]), number(match[3])];
  if (![lightness, chroma, hue].every(Number.isFinite)) return fallback;

  return oklchToHex(lightness, chroma, hue);
}
