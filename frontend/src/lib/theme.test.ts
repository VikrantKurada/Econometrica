import { beforeEach, describe, expect, it, vi } from "vitest";

import { THEME_STORAGE_KEY } from "./theme";

function stubPrefersDark(dark: boolean): void {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: dark && query.includes("dark"),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

// The store reads localStorage once at module scope, so each case needs a fresh
// module instance rather than a fresh store.
async function loadStore() {
  vi.resetModules();
  const module = await import("./theme");
  return module;
}

describe("theme", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    document.documentElement.removeAttribute("data-theme");
  });

  it("follows the OS preference when nothing has been chosen", async () => {
    stubPrefersDark(true);
    const { useThemeStore } = await loadStore();

    expect(useThemeStore.getState().preference).toBe("system");
    expect(useThemeStore.getState().resolved).toBe("dark");
  });

  it("falls back to light when the OS prefers light", async () => {
    stubPrefersDark(false);
    const { useThemeStore } = await loadStore();

    expect(useThemeStore.getState().resolved).toBe("light");
  });

  it("prefers a stored explicit choice over the OS preference", async () => {
    localStorage.setItem(THEME_STORAGE_KEY, "light");
    stubPrefersDark(true);
    const { useThemeStore } = await loadStore();

    expect(useThemeStore.getState().preference).toBe("light");
    expect(useThemeStore.getState().resolved).toBe("light");
  });

  it("toggling writes data-theme and persists the explicit choice", async () => {
    stubPrefersDark(false);
    const { useThemeStore } = await loadStore();

    useThemeStore.getState().toggle();

    expect(useThemeStore.getState().resolved).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  });

  it("stops following the OS once a choice is explicit", async () => {
    stubPrefersDark(false);
    const { useThemeStore } = await loadStore();
    useThemeStore.getState().setPreference("light");

    stubPrefersDark(true);
    useThemeStore.getState().syncWithSystem();

    expect(useThemeStore.getState().resolved).toBe("light");
  });

  it("re-resolves on an OS change while still following the OS", async () => {
    stubPrefersDark(false);
    const { useThemeStore } = await loadStore();

    stubPrefersDark(true);
    useThemeStore.getState().syncWithSystem();

    expect(useThemeStore.getState().resolved).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});
