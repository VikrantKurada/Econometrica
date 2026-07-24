import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useSelectionStore } from "../../lib/store";
import { installFakeApi, makeChat, makeProject, type FakeApi } from "../../test/fakeApi";
import { renderWithProviders } from "../../test/renderWithProviders";
import { ProjectTree } from "./ProjectTree";

let fake: FakeApi;

beforeEach(() => {
  vi.unstubAllGlobals();
  fake = installFakeApi();
  useSelectionStore.setState({
    selectedProjectId: null,
    selectedChatId: null,
    expandedProjectIds: [],
  });
});

function seedProjects() {
  const alpha = makeProject({ name: "Alpha" });
  const beta = makeProject({ name: "Beta" });
  fake.projects.push(alpha, beta);
  return { alpha, beta };
}

describe("ProjectTree", () => {
  it("renders the projects the API returns", async () => {
    seedProjects();
    renderWithProviders(<ProjectTree />);

    expect(await screen.findByRole("treeitem", { name: /alpha/i })).toBeInTheDocument();
    expect(screen.getByRole("treeitem", { name: /beta/i })).toBeInTheDocument();
  });

  it("says so when there are no projects", async () => {
    renderWithProviders(<ProjectTree />);

    expect(await screen.findByText(/no projects yet/i)).toBeInTheDocument();
  });

  it("loads a project's chats only once it is expanded", async () => {
    const { alpha } = seedProjects();
    fake.chats.push(makeChat(alpha.id, { name: "Rolling beta" }));

    const { user } = renderWithProviders(<ProjectTree />);
    await screen.findByRole("treeitem", { name: /alpha/i });

    expect(fake.callsTo("GET", "/chats")).toHaveLength(0);
    expect(screen.queryByRole("treeitem", { name: /rolling beta/i })).toBeNull();

    await user.click(screen.getByRole("button", { name: /expand alpha/i }));

    expect(await screen.findByRole("treeitem", { name: /rolling beta/i })).toBeInTheDocument();
    expect(fake.callsTo("GET", `/api/projects/${alpha.id}/chats`)).toHaveLength(1);
  });

  it("creates a project and drops straight into naming it", async () => {
    const { user } = renderWithProviders(<ProjectTree />);
    await screen.findByText(/no projects yet/i);

    await user.click(screen.getByRole("button", { name: /new project/i }));

    const input = await screen.findByRole("textbox", { name: /rename project/i });
    expect(fake.callsTo("POST", "/api/projects")).toHaveLength(1);

    await user.clear(input);
    await user.type(input, "Momentum study{Enter}");

    expect(await screen.findByRole("treeitem", { name: /momentum study/i })).toBeInTheDocument();
  });

  it("adds a chat under a project", async () => {
    const { alpha } = seedProjects();
    const { user } = renderWithProviders(<ProjectTree />);
    await screen.findByRole("treeitem", { name: /alpha/i });

    await user.click(screen.getByRole("button", { name: /new chat in alpha/i }));

    const input = await screen.findByRole("textbox", { name: /rename chat/i });
    await user.clear(input);
    await user.type(input, "Unit roots{Enter}");

    expect(await screen.findByRole("treeitem", { name: /unit roots/i })).toBeInTheDocument();
    expect(fake.callsTo("POST", `/api/projects/${alpha.id}/chats`)).toHaveLength(1);
  });

  it("renames on double click and commits on Enter", async () => {
    const { alpha } = seedProjects();
    const { user } = renderWithProviders(<ProjectTree />);

    await user.dblClick(await screen.findByText("Alpha"));

    const input = screen.getByRole("textbox", { name: /rename project/i });
    expect(input).toHaveValue("Alpha");

    await user.clear(input);
    await user.type(input, "Alpha prime{Enter}");

    expect(await screen.findByRole("treeitem", { name: /alpha prime/i })).toBeInTheDocument();
    expect(fake.callsTo("PATCH", `/api/projects/${alpha.id}`)).toHaveLength(1);
  });

  it("patches only the field that changed", async () => {
    const { alpha } = seedProjects();
    const { user } = renderWithProviders(<ProjectTree />);

    await user.dblClick(await screen.findByText("Alpha"));
    const input = screen.getByRole("textbox", { name: /rename project/i });
    await user.clear(input);
    await user.type(input, "Alpha prime{Enter}");

    await waitFor(() => expect(fake.callsTo("PATCH")).toHaveLength(1));
    const [call] = fake.callsTo("PATCH", `/api/projects/${alpha.id}`);
    expect(call?.body).toEqual({ name: "Alpha prime" });
  });

  it("restores the original name when Escape is pressed", async () => {
    seedProjects();
    const { user } = renderWithProviders(<ProjectTree />);

    await user.dblClick(await screen.findByText("Alpha"));
    const input = screen.getByRole("textbox", { name: /rename project/i });
    await user.clear(input);
    await user.type(input, "Discarded{Escape}");

    expect(await screen.findByRole("treeitem", { name: /alpha/i })).toBeInTheDocument();
    expect(screen.queryByText("Discarded")).toBeNull();
    expect(fake.callsTo("PATCH")).toHaveLength(0);
  });

  it("does not patch when the name is unchanged", async () => {
    seedProjects();
    const { user } = renderWithProviders(<ProjectTree />);

    await user.dblClick(await screen.findByText("Alpha"));
    await user.type(screen.getByRole("textbox", { name: /rename project/i }), "{Enter}");

    expect(fake.callsTo("PATCH")).toHaveLength(0);
  });

  it("asks for confirmation before deleting", async () => {
    const { alpha } = seedProjects();
    const { user } = renderWithProviders(<ProjectTree />);
    await screen.findByRole("treeitem", { name: /alpha/i });

    await user.click(screen.getByRole("button", { name: /delete alpha/i }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/alpha/i)).toBeInTheDocument();
    expect(fake.callsTo("DELETE")).toHaveLength(0);

    await user.click(within(dialog).getByRole("button", { name: /^delete$/i }));

    await waitFor(() =>
      expect(fake.callsTo("DELETE", `/api/projects/${alpha.id}`)).toHaveLength(1),
    );
    await waitFor(() => expect(screen.queryByRole("treeitem", { name: /alpha/i })).toBeNull());
  });

  it("deletes nothing when the confirmation is dismissed", async () => {
    seedProjects();
    const { user } = renderWithProviders(<ProjectTree />);
    await screen.findByRole("treeitem", { name: /alpha/i });

    await user.click(screen.getByRole("button", { name: /delete alpha/i }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /cancel/i }));

    expect(fake.callsTo("DELETE")).toHaveLength(0);
    expect(screen.getByRole("treeitem", { name: /alpha/i })).toBeInTheDocument();
  });

  it("surfaces a 422 from a blank name instead of failing silently", async () => {
    seedProjects();
    const { user } = renderWithProviders(<ProjectTree />);

    await user.dblClick(await screen.findByText("Alpha"));
    const input = screen.getByRole("textbox", { name: /rename project/i });
    await user.clear(input);
    await user.type(input, "{Enter}");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/name must not be blank/i);
    // The row keeps the name the server still holds.
    expect(screen.getByRole("treeitem", { name: /alpha/i })).toBeInTheDocument();
  });

  it("records the selected project and chat in the store", async () => {
    const { alpha } = seedProjects();
    const chat = makeChat(alpha.id, { name: "Rolling beta" });
    fake.chats.push(chat);

    const { user } = renderWithProviders(<ProjectTree />);
    await user.click(await screen.findByRole("treeitem", { name: /alpha/i }));

    expect(useSelectionStore.getState().selectedProjectId).toBe(alpha.id);
    expect(useSelectionStore.getState().selectedChatId).toBeNull();

    await user.click(screen.getByRole("button", { name: /expand alpha/i }));
    await user.click(await screen.findByRole("treeitem", { name: /rolling beta/i }));

    expect(useSelectionStore.getState().selectedChatId).toBe(chat.id);
    expect(useSelectionStore.getState().selectedProjectId).toBe(alpha.id);
  });

  it("navigates and renames from the keyboard alone", async () => {
    seedProjects();
    const { user } = renderWithProviders(<ProjectTree />);

    const alphaRow = await screen.findByRole("treeitem", { name: /alpha/i });

    // The whole tree is one tab stop: New project, then the tree itself.
    await user.tab();
    await user.tab();
    expect(alphaRow).toHaveFocus();

    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("treeitem", { name: /beta/i })).toHaveFocus();

    await user.keyboard("{ArrowUp}");
    expect(alphaRow).toHaveFocus();

    await user.keyboard("{F2}");
    expect(screen.getByRole("textbox", { name: /rename project/i })).toHaveValue("Alpha");
  });

  it("expands and collapses a project with the arrow keys", async () => {
    const { alpha } = seedProjects();
    fake.chats.push(makeChat(alpha.id, { name: "Rolling beta" }));

    const { user } = renderWithProviders(<ProjectTree />);
    const alphaRow = await screen.findByRole("treeitem", { name: /alpha/i });
    await user.tab();
    await user.tab();

    expect(alphaRow).toHaveAttribute("aria-expanded", "false");

    await user.keyboard("{ArrowRight}");
    expect(await screen.findByRole("treeitem", { name: /rolling beta/i })).toBeInTheDocument();
    expect(screen.getByRole("treeitem", { name: /alpha/i })).toHaveAttribute(
      "aria-expanded",
      "true",
    );

    await user.keyboard("{ArrowLeft}");
    expect(screen.queryByRole("treeitem", { name: /rolling beta/i })).toBeNull();
  });
});
