import { vi, type Mock } from "vitest";

import type { Chat, Dataset, Project, Upload } from "../lib/types";

/**
 * A stand-in for the backend, installed over `globalThis.fetch`.
 *
 * MSW would be the other option. `fetch` is stubbed directly here because the
 * whole app funnels through one thin request helper, so intercepting at the
 * fetch boundary still exercises the real URL, method, headers and serialised
 * body — which is exactly what the "PATCH sends only the changed field" and
 * three-state-toggle assertions need to see. It also avoids a second network
 * stack and a server lifecycle in every test file.
 */

export interface RecordedCall {
  method: string;
  path: string;
  body: unknown;
}

export interface FakeApi {
  projects: Project[];
  chats: Chat[];
  datasets: Dataset[];
  uploads: Upload[];
  calls: RecordedCall[];
  fetchMock: Mock;
  /** Calls narrowed to a method and an optional path substring. */
  callsTo: (method: string, pathIncludes?: string) => RecordedCall[];
}

let counter = 0;

function nextId(prefix: string): string {
  counter += 1;
  return `${prefix}-${String(counter).padStart(4, "0")}-0000-0000-000000000000`;
}

const NOW = "2026-07-24T12:00:00Z";

export function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: nextId("p"),
    name: "Project",
    description: null,
    web_search_enabled: false,
    mcp_enabled: false,
    code_sandbox_enabled: false,
    validation_tier: "single",
    model_assignments: {},
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

export function makeDataset(projectId: string, overrides: Partial<Dataset> = {}): Dataset {
  return {
    id: nextId("d"),
    project_id: projectId,
    name: "prices.csv",
    source_label: "Uploaded file",
    rows: 100,
    column_roles: { date: "date", AAA: "price" },
    fingerprint: "abc123",
    created_at: NOW,
    symbols: ["AAA"],
    ...overrides,
  };
}

export function makeUpload(projectId: string, overrides: Partial<Upload> = {}): Upload {
  return {
    id: nextId("u"),
    project_id: projectId,
    filename: "prices.csv",
    profile: {
      filename: "prices.csv",
      format: "csv",
      rows: 100,
      layout: "wide",
      delimiter: ",",
      columns: [
        {
          name: "date", dtype: "datetime", present: 100, missing: 0, unique: 100,
          minimum: null, maximum: null, sample: ["2020-01-01"],
          parses_as_date: true, decimal_comma: false, candidates: [],
        },
        {
          name: "AAA", dtype: "number", present: 100, missing: 0, unique: 99,
          minimum: 90, maximum: 110, sample: ["100.5"],
          parses_as_date: false, decimal_comma: false, candidates: [],
        },
      ],
    },
    proposal: {
      roles: { date: "date", AAA: "price" },
      rationale: { date: "parses as a date", AAA: "numeric, price-like" },
      ambiguous: [],
    },
    consulted_model: false,
    confirmed: false,
    mapping: null,
    observations: null,
    symbols: [],
    fields: [],
    ...overrides,
  };
}

export function makeChat(projectId: string, overrides: Partial<Chat> = {}): Chat {
  return {
    id: nextId("c"),
    project_id: projectId,
    name: "Chat",
    // null, not false: no override, inherit from the project.
    web_search_enabled: null,
    mcp_enabled: null,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function json(status: number, body: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** The shape FastAPI returns when a Pydantic validator rejects a field. */
function unprocessable(field: string, message: string): Response {
  return json(422, {
    detail: [{ type: "value_error", loc: ["body", field], msg: `Value error, ${message}`, input: "" }],
  });
}

function blankName(name: unknown): boolean {
  return typeof name !== "string" || name.trim() === "";
}

export function installFakeApi(
  seed: { projects?: Project[]; chats?: Chat[]; datasets?: Dataset[] } = {},
): FakeApi {
  const state: FakeApi = {
    projects: [...(seed.projects ?? [])],
    chats: [...(seed.chats ?? [])],
    datasets: [...(seed.datasets ?? [])],
    uploads: [],
    calls: [],
    fetchMock: vi.fn(),
    callsTo: (method, pathIncludes) =>
      state.calls.filter(
        (call) =>
          call.method === method && (pathIncludes === undefined || call.path.includes(pathIncludes)),
      ),
  };

  state.fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    const body: unknown =
      typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined;

    state.calls.push({ method, path, body });

    const record = body as Record<string, unknown> | undefined;

    if (path === "/api/health") return json(200, { status: "ok", version: "0.1.0" });

    if (path === "/api/projects" && method === "GET") return json(200, state.projects);

    if (path === "/api/projects" && method === "POST") {
      if (blankName(record?.name)) return unprocessable("name", "name must not be blank");
      const project = makeProject({
        name: String(record?.name),
        description: (record?.description as string | null | undefined) ?? null,
      });
      state.projects.push(project);
      return json(201, project);
    }

    const projectChats = /^\/api\/projects\/([^/]+)\/chats$/.exec(path);
    if (projectChats) {
      const projectId = projectChats[1] as string;
      if (method === "GET") {
        return json(
          200,
          state.chats.filter((chat) => chat.project_id === projectId),
        );
      }
      if (method === "POST") {
        if (blankName(record?.name)) return unprocessable("name", "name must not be blank");
        const chat = makeChat(projectId, { name: String(record?.name) });
        state.chats.push(chat);
        return json(201, chat);
      }
    }

    const projectById = /^\/api\/projects\/([^/]+)$/.exec(path);
    if (projectById) {
      const project = state.projects.find((candidate) => candidate.id === projectById[1]);
      if (!project) return json(404, { detail: "Project not found" });

      if (method === "GET") return json(200, project);
      if (method === "PATCH") {
        if ("name" in (record ?? {}) && blankName(record?.name)) {
          return unprocessable("name", "name must not be blank");
        }
        Object.assign(project, record, { updated_at: NOW });
        return json(200, project);
      }
      if (method === "DELETE") {
        state.projects = state.projects.filter((candidate) => candidate.id !== project.id);
        state.chats = state.chats.filter((chat) => chat.project_id !== project.id);
        return new Response(null, { status: 204 });
      }
    }

    const chatById = /^\/api\/chats\/([^/]+)$/.exec(path);
    if (chatById) {
      const chat = state.chats.find((candidate) => candidate.id === chatById[1]);
      if (!chat) return json(404, { detail: "Chat not found" });

      if (method === "GET") return json(200, chat);
      if (method === "PATCH") {
        if ("name" in (record ?? {}) && blankName(record?.name)) {
          return unprocessable("name", "name must not be blank");
        }
        Object.assign(chat, record, { updated_at: NOW });
        return json(200, chat);
      }
      if (method === "DELETE") {
        state.chats = state.chats.filter((candidate) => candidate.id !== chat.id);
        return new Response(null, { status: 204 });
      }
    }

    const projectDatasets = /^\/api\/projects\/([^/]+)\/datasets$/.exec(path);
    if (projectDatasets && method === "GET") {
      const projectId = projectDatasets[1] as string;
      return json(
        200,
        state.datasets.filter((dataset) => dataset.project_id === projectId),
      );
    }

    const projectUploads = /^\/api\/projects\/([^/]+)\/uploads$/.exec(path);
    if (projectUploads && method === "POST") {
      const upload = makeUpload(projectUploads[1] as string);
      state.uploads.push(upload);
      return json(201, upload);
    }

    const uploadConfirm = /^\/api\/uploads\/([^/]+)\/confirm$/.exec(path);
    if (uploadConfirm && method === "POST") {
      const upload = state.uploads.find((candidate) => candidate.id === uploadConfirm[1]);
      if (!upload) return json(404, { detail: "Upload not found" });
      state.datasets.push(makeDataset(upload.project_id, { name: upload.filename }));
      return json(200, {
        ...upload,
        confirmed: true,
        observations: 100,
        symbols: ["AAA"],
        fields: ["price"],
      });
    }

    return json(404, { detail: `No fake route for ${method} ${path}` });
  });

  vi.stubGlobal("fetch", state.fetchMock);
  return state;
}
