import { create } from "zustand";
import { persist } from "zustand/middleware";

export type PaneId = "projects" | "canvas" | "chat";

export const PANE_ORDER: readonly PaneId[] = ["projects", "canvas", "chat"];

export const PANE_LABELS: Record<PaneId, string> = {
  projects: "projects",
  canvas: "canvas",
  chat: "chat",
};

interface LayoutState {
  collapsed: Record<PaneId, boolean>;
  setPaneCollapsed: (pane: PaneId, collapsed: boolean) => void;
  togglePane: (pane: PaneId) => void;
}

/**
 * Which panes are collapsed. Kept in a store rather than component state so it
 * survives the routing and pane content that later phases add, and persisted so
 * a reload does not undo the user's arrangement.
 */
export const useLayoutStore = create<LayoutState>()(
  persist(
    (set, get) => ({
      collapsed: { projects: false, canvas: false, chat: false },

      setPaneCollapsed: (pane, collapsed) => {
        const next = { ...get().collapsed, [pane]: collapsed };
        // Collapsing the last pane standing would leave an empty window with
        // no obvious way back, so that one move is refused.
        if (PANE_ORDER.every((id) => next[id])) return;
        set({ collapsed: next });
      },

      togglePane: (pane) => {
        get().setPaneCollapsed(pane, !get().collapsed[pane]);
      },
    }),
    {
      name: "econometrica.layout",
      partialize: (state) => ({ collapsed: state.collapsed }),
    },
  ),
);
