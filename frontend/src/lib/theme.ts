import { create } from "zustand";

export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

/** Read by the pre-paint script in index.html, so the value stays a bare string. */
export const THEME_STORAGE_KEY = "econometrica.theme";

const DARK_QUERY = "(prefers-color-scheme: dark)";

function readStoredPreference(): ThemePreference {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  } catch {
    return "system";
  }
}

function prefersDark(): boolean {
  return window.matchMedia?.(DARK_QUERY).matches ?? false;
}

export function resolveTheme(preference: ThemePreference): ResolvedTheme {
  if (preference === "system") return prefersDark() ? "dark" : "light";
  return preference;
}

function applyTheme(resolved: ResolvedTheme): void {
  document.documentElement.dataset.theme = resolved;
}

interface ThemeState {
  preference: ThemePreference;
  resolved: ResolvedTheme;
  /** Set an explicit preference, or hand control back to the OS with "system". */
  setPreference: (preference: ThemePreference) => void;
  /** Flip to whichever theme is not currently showing, making the choice explicit. */
  toggle: () => void;
  /** Re-resolve after the OS preference changed. No-op unless following the OS. */
  syncWithSystem: () => void;
}

const initialPreference = readStoredPreference();

export const useThemeStore = create<ThemeState>((set, get) => ({
  preference: initialPreference,
  resolved: resolveTheme(initialPreference),

  setPreference: (preference) => {
    try {
      if (preference === "system") localStorage.removeItem(THEME_STORAGE_KEY);
      else localStorage.setItem(THEME_STORAGE_KEY, preference);
    } catch {
      /* storage unavailable: the choice simply will not survive a reload */
    }
    const resolved = resolveTheme(preference);
    applyTheme(resolved);
    set({ preference, resolved });
  },

  toggle: () => {
    get().setPreference(get().resolved === "dark" ? "light" : "dark");
  },

  syncWithSystem: () => {
    if (get().preference !== "system") return;
    const resolved = resolveTheme("system");
    applyTheme(resolved);
    set({ resolved });
  },
}));

/**
 * Applies the resolved theme and keeps following the OS while no explicit
 * preference is set. Returns an unsubscribe function.
 */
export function initTheme(): () => void {
  applyTheme(useThemeStore.getState().resolved);

  const media = window.matchMedia?.(DARK_QUERY);
  if (!media?.addEventListener) return () => {};

  const onChange = (): void => useThemeStore.getState().syncWithSystem();
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}
