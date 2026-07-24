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

interface SelectionState {
  selectedProjectId: string | null;
  /** Null when a project is selected but none of its chats is. */
  selectedChatId: string | null;
  /** Which projects show their chats. An array so it survives JSON. */
  expandedProjectIds: string[];
  selectProject: (projectId: string) => void;
  selectChat: (projectId: string, chatId: string) => void;
  setProjectExpanded: (projectId: string, expanded: boolean) => void;
  toggleProjectExpanded: (projectId: string) => void;
  /** Drop every trace of a project that no longer exists. */
  forgetProject: (projectId: string) => void;
  forgetChat: (chatId: string) => void;
}

/**
 * What the user is looking at. Separate from the server cache on purpose: the
 * selection outlives any particular fetch, and later phases read it to decide
 * which conversation and artifact set to show.
 */
export const useSelectionStore = create<SelectionState>()(
  persist(
    (set, get) => ({
      selectedProjectId: null,
      selectedChatId: null,
      expandedProjectIds: [],

      selectProject: (projectId) => set({ selectedProjectId: projectId, selectedChatId: null }),

      selectChat: (projectId, chatId) =>
        set({ selectedProjectId: projectId, selectedChatId: chatId }),

      setProjectExpanded: (projectId, expanded) => {
        const current = get().expandedProjectIds;
        if (expanded === current.includes(projectId)) return;
        set({
          expandedProjectIds: expanded
            ? [...current, projectId]
            : current.filter((id) => id !== projectId),
        });
      },

      toggleProjectExpanded: (projectId) => {
        get().setProjectExpanded(projectId, !get().expandedProjectIds.includes(projectId));
      },

      forgetProject: (projectId) =>
        set((state) => ({
          expandedProjectIds: state.expandedProjectIds.filter((id) => id !== projectId),
          selectedProjectId: state.selectedProjectId === projectId ? null : state.selectedProjectId,
          selectedChatId: state.selectedProjectId === projectId ? null : state.selectedChatId,
        })),

      forgetChat: (chatId) =>
        set((state) => ({
          selectedChatId: state.selectedChatId === chatId ? null : state.selectedChatId,
        })),
    }),
    { name: "econometrica.selection" },
  ),
);
