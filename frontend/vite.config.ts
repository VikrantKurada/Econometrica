/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // The API is proxied so the browser only ever talks to one origin. Nothing
    // in the app needs an absolute base URL and the backend needs no CORS.
    //
    // The target is 127.0.0.1 rather than localhost on purpose. Node resolves
    // "localhost" verbatim, which on Windows hands back ::1 first — and ::1:8000
    // is whatever else happens to be bound there (a WSL service, say), while
    // `uvicorn --port 8000` binds 127.0.0.1. Naming the address removes the
    // coin flip. Override with ECONOMETRICA_API_URL if the backend moves.
    proxy: {
      "/api": {
        target: process.env.ECONOMETRICA_API_URL ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
    css: false,
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
