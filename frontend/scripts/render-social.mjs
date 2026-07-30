/**
 * Rasterises the social-preview cards into `docs/assets/`, because GitHub's
 * Settings → General → Social preview takes an image upload and will not
 * accept an SVG.
 *
 * It lives under `frontend/` rather than beside the SVGs it reads because ESM
 * resolves a bare import from the importing module's own directory, not the
 * working directory — and `frontend/node_modules` is the only one in this
 * repository. A copy in `docs/assets/` cannot find playwright however it is
 * invoked.
 *
 * Exactly 1280x640 at scale 1, the size GitHub documents. Rendering at 2x
 * would be sharper on a retina display but is not what the field expects, and
 * GitHub scales it back down anyway.
 *
 * Run from `frontend/`, after `build_capability_map.py`:
 *
 *     node scripts/render-social.mjs
 */
import { chromium } from "@playwright/test";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const assets = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "docs", "assets");

const browser = await chromium.launch();
for (const theme of ["light", "dark"]) {
  const page = await browser.newPage({
    viewport: { width: 1280, height: 640 },
    deviceScaleFactor: 1,
  });
  await page.goto(pathToFileURL(join(assets, `social-preview-${theme}.svg`)).href);
  await page.waitForTimeout(300);
  await page.screenshot({ path: join(assets, `social-preview-${theme}.png`) });
  await page.close();
  console.log(`wrote docs/assets/social-preview-${theme}.png`);
}
await browser.close();
