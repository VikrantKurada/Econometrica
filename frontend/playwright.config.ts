import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end smoke tests: real browser, real backend, real Postgres.
 *
 * Both servers are managed here so `npm run test:e2e` works from a cold start,
 * and `reuseExistingServer` means an already-running dev environment is used
 * as-is rather than fought over. Chromium only — this is a local single-user
 * tool, not a cross-browser product.
 */
export default defineConfig({
  testDir: "./e2e",
  // The specs share one real database, so they must not interleave.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: "list",
  // Cold Windows starts pay for Vite's first-load transform; give the test
  // itself headroom beyond the 30s default.
  timeout: 60_000,
  use: {
    // One origin for pages and API cleanup alike: the Vite dev server, whose
    // /api proxy reaches the backend exactly the way the app does.
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      // Port 8100, not the backend's usual 8000: Playwright's readiness poll
      // uses a keep-alive agent, so if anything else answers the port while
      // uvicorn is still starting (on one dev box a Docker container holds
      // 0.0.0.0:8000), the first poll's socket gets pinned to that stranger
      // and every later poll reuses it — uvicorn comes up and is never asked.
      // A dedicated port means the poll is refused until uvicorn owns it.
      command: "uv run uvicorn econometrica.main:app --port 8100",
      cwd: "../backend",
      url: "http://127.0.0.1:8100/api/health",
      // The analysis gate needs prices, and the real market-data adapters are
      // Phase 6. This selects the generator instead — every run built on it
      // carries a `synthetic_data` risk flag, which the spec asserts on, so
      // the gate proves the honesty seam as well as the pipeline.
      env: { ECONOMETRICA_PRICE_SOURCE: "synthetic" },
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
    {
      // --host 127.0.0.1 on purpose: left to its default "localhost", Vite
      // binds only the first address the OS resolves — ::1 on this Windows
      // box — and the readiness poll against 127.0.0.1 then never succeeds.
      // Naming the address removes the coin flip, same reasoning as the
      // proxy target in vite.config.ts. --strictPort so a taken port fails
      // loudly instead of silently drifting to 5174 while we poll 5173.
      command: "npm run dev -- --host 127.0.0.1 --strictPort",
      url: "http://127.0.0.1:5173",
      // Aim the /api proxy at the e2e backend above; vite.config.ts already
      // honours this override.
      env: { ECONOMETRICA_API_URL: "http://127.0.0.1:8100" },
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      // Vite reports readiness on stdout, which Playwright ignores by
      // default; piping it makes "why did the webServer time out?" visible.
      stdout: "pipe",
    },
  ],
});
