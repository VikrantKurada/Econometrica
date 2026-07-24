import { Columns3, PanelLeft, PanelRight } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { PANE_ORDER, useLayoutStore, type PaneId } from "../../lib/store";
import { IconButton } from "../ui/IconButton";

const ICONS: Record<PaneId, LucideIcon> = {
  projects: PanelLeft,
  canvas: Columns3,
  chat: PanelRight,
};

/**
 * The collapse controls live here, outside the panes themselves, so a collapsed
 * pane still has a visible way back. Labels flip with state, which is also what
 * makes them addressable in tests and by voice control.
 */
export function PaneToggles() {
  const collapsed = useLayoutStore((state) => state.collapsed);
  const togglePane = useLayoutStore((state) => state.togglePane);

  return (
    <div className="flex items-center gap-0.5">
      {PANE_ORDER.map((pane) => (
        <IconButton
          key={pane}
          icon={ICONS[pane]}
          label={`${collapsed[pane] ? "Expand" : "Collapse"} ${pane}`}
          active={!collapsed[pane]}
          onClick={() => togglePane(pane)}
        />
      ))}
    </div>
  );
}
