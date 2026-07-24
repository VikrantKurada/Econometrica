import * as Tooltip from "@radix-ui/react-tooltip";
import { Fragment, type ReactNode } from "react";
import { Group, Panel, Separator, useDefaultLayout } from "react-resizable-panels";

import { PANE_ORDER, useLayoutStore, type PaneId } from "../../lib/store";
import { ThemeToggle } from "../ThemeToggle";
import { CanvasPane } from "./CanvasPane";
import { ChatPane } from "./ChatPane";
import { PaneToggles } from "./PaneToggles";
import { SidePane } from "./SidePane";

export interface AppShellProps {
  /** Left pane content. Falls back to the pane's own empty state. */
  projects?: ReactNode;
  /** Centre pane content. */
  canvas?: ReactNode;
  /** Right pane content. */
  chat?: ReactNode;
  /** Status bar content, e.g. the backend health indicator. */
  status?: ReactNode;
}

/** Percentages of the group; strings without units are read as percentages. */
const SIZES: Record<PaneId, { defaultSize: string; minSize: string }> = {
  projects: { defaultSize: "18", minSize: "12" },
  canvas: { defaultSize: "54", minSize: "30" },
  chat: { defaultSize: "28", minSize: "20" },
};

const GROUP_ID = "econometrica.workbench";

/**
 * The application frame: a title bar, three resizable panes, a status bar.
 *
 * Panes are slots rather than hard-wired content, so this stays a layout
 * concern and can be rendered on its own in a test. Collapsing a pane unmounts
 * it — a zero-width pane left in the accessibility tree would be a lie to
 * anyone not using a mouse.
 */
export function AppShell({ projects, canvas, chat, status }: AppShellProps) {
  const collapsed = useLayoutStore((state) => state.collapsed);

  const content: Record<PaneId, ReactNode> = {
    projects: <SidePane>{projects}</SidePane>,
    canvas: <CanvasPane>{canvas}</CanvasPane>,
    chat: <ChatPane>{chat}</ChatPane>,
  };

  const visible = PANE_ORDER.filter((pane) => !collapsed[pane]);

  // Layouts are stored per visible-pane combination, so collapsing and
  // re-expanding a pane returns the others to where the user left them.
  const { defaultLayout, onLayoutChanged } = useDefaultLayout({
    id: GROUP_ID,
    panelIds: [...visible],
    storage: localStorage,
    onlySaveAfterUserInteractions: true,
  });

  return (
    <Tooltip.Provider delayDuration={400} skipDelayDuration={200}>
      <div className="flex h-full flex-col bg-surface-0 text-text-primary">
        <header className="flex h-11 shrink-0 items-center gap-3 border-b border-border bg-surface-1 px-3">
          <span className="text-sm font-semibold tracking-tight">Econometrica</span>
          <div className="ml-auto flex items-center gap-2">
            <PaneToggles />
            <div aria-hidden="true" className="h-4 w-px bg-border" />
            <ThemeToggle />
          </div>
        </header>

        <Group
          id={GROUP_ID}
          orientation="horizontal"
          className="min-h-0 flex-1"
          defaultLayout={defaultLayout}
          onLayoutChanged={onLayoutChanged}
        >
          {visible.map((pane, index) => (
            <Fragment key={pane}>
              {index > 0 ? (
                <Separator
                  aria-label={`Resize ${visible[index - 1]} and ${pane} panes`}
                  className={
                    "w-px shrink-0 bg-border transition-colors duration-100 " +
                    "hover:bg-accent focus-visible:bg-accent focus-visible:outline-none"
                  }
                />
              ) : null}
              <Panel id={pane} minSize={SIZES[pane].minSize} defaultSize={SIZES[pane].defaultSize}>
                {content[pane]}
              </Panel>
            </Fragment>
          ))}
        </Group>

        <footer className="flex h-6 shrink-0 items-center gap-4 border-t border-border bg-surface-1 px-3 text-2xs">
          {status}
        </footer>
      </div>
    </Tooltip.Provider>
  );
}
