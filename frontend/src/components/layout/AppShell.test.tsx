import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useLayoutStore } from "../../lib/store";
import { AppShell } from "./AppShell";

// The layout store is a module singleton, so a collapse in one test would leak
// into the next without this.
beforeEach(() => {
  useLayoutStore.setState({ collapsed: { projects: false, canvas: false, chat: false } });
});

describe("AppShell", () => {
  it("renders all three panes", () => {
    render(<AppShell />);
    expect(screen.getByRole("navigation", { name: /projects/i })).toBeTruthy();
    expect(screen.getByRole("region", { name: /artifacts/i })).toBeTruthy();
    expect(screen.getByRole("complementary", { name: /chat/i })).toBeTruthy();
  });

  it("collapses the projects pane when the toggle is clicked", async () => {
    render(<AppShell />);
    await userEvent.click(screen.getByRole("button", { name: /collapse projects/i }));
    expect(screen.queryByRole("navigation", { name: /projects/i })).toBeNull();
  });

  it("brings a collapsed pane back", async () => {
    const user = userEvent.setup();
    render(<AppShell />);

    await user.click(screen.getByRole("button", { name: /collapse chat/i }));
    expect(screen.queryByRole("complementary", { name: /chat/i })).toBeNull();

    await user.click(screen.getByRole("button", { name: /expand chat/i }));
    expect(screen.getByRole("complementary", { name: /chat/i })).toBeTruthy();
  });

  it("puts a keyboard-reachable resize handle between each adjacent pair of panes", async () => {
    const user = userEvent.setup();
    render(<AppShell />);

    const handles = screen.getAllByRole("separator");
    expect(handles).toHaveLength(2);
    for (const handle of handles) {
      expect(handle).toHaveAttribute("tabindex", "0");
      expect(handle).toHaveAttribute("aria-orientation", "vertical");
    }

    // Two panes need one handle, not two dangling at the edges.
    await user.click(screen.getByRole("button", { name: /collapse canvas/i }));
    expect(screen.getAllByRole("separator")).toHaveLength(1);
  });

  it("refuses to collapse the last pane standing", async () => {
    const user = userEvent.setup();
    render(<AppShell />);

    await user.click(screen.getByRole("button", { name: /collapse projects/i }));
    await user.click(screen.getByRole("button", { name: /collapse canvas/i }));
    await user.click(screen.getByRole("button", { name: /collapse chat/i }));

    expect(screen.getByRole("complementary", { name: /chat/i })).toBeTruthy();
    expect(screen.queryAllByRole("separator")).toHaveLength(0);
  });

  it("writes collapse state to localStorage", async () => {
    const user = userEvent.setup();
    render(<AppShell />);

    await user.click(screen.getByRole("button", { name: /collapse projects/i }));

    const raw = localStorage.getItem("econometrica.layout");
    expect(raw).not.toBeNull();
    const stored = JSON.parse(raw ?? "{}") as { state: { collapsed: Record<string, boolean> } };
    expect(stored.state.collapsed).toEqual({ projects: true, canvas: false, chat: false });
  });

  it("restores collapse state from localStorage on a fresh load", async () => {
    localStorage.setItem(
      "econometrica.layout",
      JSON.stringify({ state: { collapsed: { projects: true, canvas: false, chat: false } }, version: 0 }),
    );

    vi.resetModules();
    const { AppShell: FreshAppShell } = await import("./AppShell");
    render(<FreshAppShell />);

    expect(screen.queryByRole("navigation", { name: /projects/i })).toBeNull();
    expect(screen.getByRole("region", { name: /artifacts/i })).toBeTruthy();
  });
});
