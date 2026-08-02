import { beforeEach, describe, expect, it, vi } from "vitest";

import { installFakeApi, makeChat, makeDataset, makeProject, type FakeApi } from "../test/fakeApi";
import { ApiError, api, formatApiError } from "./api";

let fake: FakeApi;

beforeEach(() => {
  vi.unstubAllGlobals();
  fake = installFakeApi();
});

describe("api", () => {
  it("lists projects from the proxied path", async () => {
    fake.projects.push(makeProject({ name: "Momentum" }));

    const projects = await api.listProjects();

    expect(projects.map((project) => project.name)).toEqual(["Momentum"]);
    expect(fake.calls[0]).toMatchObject({ method: "GET", path: "/api/projects" });
  });

  it("lists a project's datasets from the proxied path", async () => {
    const project = makeProject();
    fake.projects.push(project);
    fake.datasets.push(makeDataset(project.id, { name: "prices.csv" }));

    const datasets = await api.listDatasets(project.id);

    expect(datasets.map((d) => d.name)).toEqual(["prices.csv"]);
    expect(fake.calls.at(-1)).toMatchObject({
      method: "GET",
      path: `/api/projects/${project.id}/datasets`,
    });
  });

  it("sends only the fields given to a project patch", async () => {
    const project = makeProject({ name: "Old", description: "keep me" });
    fake.projects.push(project);

    await api.updateProject(project.id, { name: "New" });

    const [call] = fake.callsTo("PATCH", "/api/projects/");
    expect(call?.body).toEqual({ name: "New" });
  });

  it("keeps an explicit null in a chat patch so the override can be cleared", async () => {
    const project = makeProject();
    const chat = makeChat(project.id, { web_search_enabled: true });
    fake.projects.push(project);
    fake.chats.push(chat);

    await api.updateChat(chat.id, { web_search_enabled: null });

    const [call] = fake.callsTo("PATCH", "/api/chats/");
    // JSON.stringify drops undefined but keeps null, and that difference is
    // the whole three-state contract: absent = unchanged, null = inherit.
    expect(call?.body).toEqual({ web_search_enabled: null });
    expect(Object.keys(call?.body as object)).toContain("web_search_enabled");
  });

  it("distinguishes an explicit false from an inherited null", async () => {
    const project = makeProject();
    const chat = makeChat(project.id);
    fake.projects.push(project);
    fake.chats.push(chat);

    const updated = await api.updateChat(chat.id, { mcp_enabled: false });

    expect(updated.mcp_enabled).toBe(false);
    expect(updated.web_search_enabled).toBeNull();
  });

  it("turns a 422 into an ApiError carrying a readable message", async () => {
    await expect(api.createProject({ name: "  " })).rejects.toThrowError(ApiError);

    await api.createProject({ name: "   " }).catch((error: unknown) => {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(422);
      expect((error as ApiError).message).toBe("name: name must not be blank");
    });
  });

  it("returns nothing for a 204", async () => {
    const project = makeProject();
    fake.projects.push(project);

    await expect(api.deleteProject(project.id)).resolves.toBeUndefined();
    expect(fake.callsTo("DELETE")).toHaveLength(1);
  });
});

describe("formatApiError", () => {
  it("reads a plain string detail", () => {
    expect(formatApiError(404, { detail: "Project not found" })).toBe("Project not found");
  });

  it("joins several validation issues", () => {
    const body = {
      detail: [
        { loc: ["body", "name"], msg: "Value error, name must not be blank" },
        { loc: ["body", "validation_tier"], msg: "Input should be 'single'" },
      ],
    };
    expect(formatApiError(422, body)).toBe(
      "name: name must not be blank; validation_tier: Input should be 'single'",
    );
  });

  it("falls back to the status when the body says nothing useful", () => {
    expect(formatApiError(500, undefined)).toBe("Request failed (500)");
  });
});
