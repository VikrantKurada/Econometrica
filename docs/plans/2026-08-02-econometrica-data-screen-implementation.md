# Data Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a project's uploads a home in the app — select a project and the centre pane shows its datasets and the upload → column-mapping → confirm flow.

**Architecture:** A new `ProjectData` container renders in `App.tsx`'s canvas slot when a project (but no chat) is selected — the empty state the canvas already falls to. It lists datasets (`api.listDatasets`), and on upload swaps to the existing `ColumnMapping` screen, confirming through `api.confirmUpload` and refetching. No change to the three-pane shell.

**Tech Stack:** React 18 + TypeScript, TanStack Query, Zustand (selection store), Tailwind, Vitest + Testing Library, the shared `test/fakeApi.ts` fetch stub.

**Design note:** `docs/plans/2026-08-02-econometrica-data-screen-design.md`.

## Global Constraints

- **TDD, strictly.** Write the failing test, run it red, then implement.
- Frontend commands run from `frontend/`: `npx vitest run`, `npx tsc --noEmit`, `npm run lint` (oxlint).
- **The gate is all three:** vitest green, `tsc --noEmit` clean, `oxlint` clean.
- Follow existing patterns: React-Query via `queryKeys` from `lib/queryClient`; components under `components/`; tests beside them with `renderWithProviders` and `installFakeApi`.
- **Commit with `git commit -F <file>`** (heredoc), ending each body with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Branch:** `feat/foundation`.
- After the code lands, **verify in the real browser** — the standing rule.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `frontend/src/lib/types.ts` | Modify. Add `Dataset`. | 1 |
| `frontend/src/lib/api.ts` | Modify. Add `listDatasets`. | 1 |
| `frontend/src/lib/queryClient.ts` | Modify. Add `queryKeys.datasets`. | 1 |
| `frontend/src/lib/api.test.ts` | Modify. Test `listDatasets`. | 1 |
| `frontend/src/components/data/DatasetList.tsx` | New. The dataset rows / empty hint. | 2 |
| `frontend/src/components/data/DatasetList.test.tsx` | New. | 2 |
| `frontend/src/test/fakeApi.ts` | Modify. datasets/uploads state + routes + `makeDataset`/`makeUpload`. | 3 |
| `frontend/src/components/data/ProjectData.tsx` | New. The flow container. | 3 |
| `frontend/src/components/data/ProjectData.test.tsx` | New. | 3 |
| `frontend/src/App.tsx` | Modify. Render `ProjectData` in the canvas slot. | 4 |

---

## Task 1: `Dataset` type, `listDatasets`, and the query key

**Files:**
- Modify: `frontend/src/lib/types.ts`, `frontend/src/lib/api.ts`, `frontend/src/lib/queryClient.ts`
- Test: `frontend/src/lib/api.test.ts`

**Interfaces:**
- Produces: `Dataset` (interface); `api.listDatasets(projectId: string): Promise<Dataset[]>`; `queryKeys.datasets(projectId: string)`.

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/lib/api.test.ts` (inside `describe("api", ...)`):

```ts
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
```

Update the import at the top of the file to include `makeDataset`:
```ts
import { installFakeApi, makeChat, makeDataset, makeProject, type FakeApi } from "../test/fakeApi";
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run src/lib/api.test.ts`
Expected: FAIL — `makeDataset` is not exported (and `fake.datasets` does not exist). (Task 3 adds them to `fakeApi`; this task can be committed after Task 3, or add the two fixtures first. **Do Step 3–4 of Task 3's fakeApi changes now if needed to run this test** — see note below.)

> **Ordering note:** `makeDataset` and `fake.datasets` live in `fakeApi.ts`, which Task 3 also edits. To keep this task runnable on its own, add the `Dataset` state, `makeDataset` helper, and the `GET …/datasets` route to `fakeApi.ts` **here**, and let Task 3 add only the uploads/confirm routes and `makeUpload`. The File-structure table lists `fakeApi.ts` under Task 3 for its bulk; the datasets slice belongs with this task.

Add to `frontend/src/test/fakeApi.ts` now:

- import `Dataset`:
  ```ts
  import type { Chat, Dataset, Project } from "../lib/types";
  ```
- add `datasets: Dataset[]` to the `FakeApi` interface;
- seed it: extend the `seed` param to `{ projects?; chats?; datasets? }` and set `datasets: [...(seed.datasets ?? [])]` in `state`;
- the helper:
  ```ts
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
  ```
- the route (before the final `return json(404, …)`):
  ```ts
  const projectDatasets = /^\/api\/projects\/([^/]+)\/datasets$/.exec(path);
  if (projectDatasets && method === "GET") {
    const projectId = projectDatasets[1] as string;
    return json(200, state.datasets.filter((dataset) => dataset.project_id === projectId));
  }
  ```

Re-run: FAIL — `api.listDatasets` is not a function.

- [ ] **Step 3: Implement**

`frontend/src/lib/types.ts`, in the uploads section (after `Upload`):
```ts
export interface Dataset {
  id: string;
  project_id: string;
  name: string;
  source_label: string;
  rows: number;
  column_roles: Record<string, string>;
  fingerprint: string;
  created_at: string;
  symbols: string[];
}
```

`frontend/src/lib/api.ts` — add `Dataset` to the type import, and after `confirmUpload`:
```ts
  listDatasets: (projectId: string): Promise<Dataset[]> =>
    request<Dataset[]>(`/projects/${projectId}/datasets`),
```

`frontend/src/lib/queryClient.ts`, in `queryKeys`:
```ts
  datasets: (projectId: string) => ["datasets", projectId] as const,
```

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run src/lib/api.test.ts`
Expected: PASS.

- [ ] **Step 5: Typecheck, lint, commit**

Run: `npx tsc --noEmit` and `npm run lint`
Expected: clean.

```bash
cat > /tmp/d1.txt <<'EOF'
feat(web): a Dataset type and listDatasets

The frontend has the upload calls but no way to read a project's stored
datasets. Add the Dataset type mirroring DatasetRead, api.listDatasets,
and a datasets query key -- the pieces the Data screen lists from. The
fetch stub grows a datasets store and route to match.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/lib/queryClient.ts \
  frontend/src/lib/api.test.ts frontend/src/test/fakeApi.ts
git commit -F /tmp/d1.txt && rm -f /tmp/d1.txt
```

---

## Task 2: `DatasetList` — the rows

**Files:**
- Create: `frontend/src/components/data/DatasetList.tsx`
- Test: `frontend/src/components/data/DatasetList.test.tsx`

**Interfaces:**
- Consumes: `Dataset` (Task 1).
- Produces: `DatasetList({ datasets }: { datasets: Dataset[] })`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/data/DatasetList.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { makeDataset } from "../../test/fakeApi";
import { DatasetList } from "./DatasetList";

describe("DatasetList", () => {
  it("renders a row per dataset with its facts", () => {
    render(
      <DatasetList
        datasets={[
          makeDataset("p1", { name: "prices.csv", rows: 1506, symbols: ["AAA", "BBB"] }),
        ]}
      />,
    );

    expect(screen.getByText("prices.csv")).toBeInTheDocument();
    expect(screen.getByText(/1,506 rows/)).toBeInTheDocument();
    expect(screen.getByText(/AAA, BBB/)).toBeInTheDocument();
  });

  it("shows an empty hint when a project has no data", () => {
    render(<DatasetList datasets={[]} />);

    expect(screen.getByText(/No data yet/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/components/data/DatasetList.test.tsx`
Expected: FAIL — cannot resolve `./DatasetList`.

- [ ] **Step 3: Implement**

Create `frontend/src/components/data/DatasetList.tsx`:

```tsx
import { Database } from "lucide-react";

import type { Dataset } from "../../lib/types";
import { EmptyState } from "../layout/EmptyState";

/** A project's stored datasets, or an empty hint. Presentational: it is handed
 * its data and renders it, so `ProjectData` stays about the flow. */
export function DatasetList({ datasets }: { datasets: Dataset[] }) {
  if (datasets.length === 0) {
    return (
      <EmptyState
        icon={Database}
        title="No data yet"
        hint="Upload a CSV or Excel file to analyse it in a chat."
      />
    );
  }

  return (
    <ul aria-label="Datasets" className="flex flex-col gap-2">
      {datasets.map((dataset) => (
        <li key={dataset.id} className="rounded border border-border bg-surface-1 p-3">
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-sm font-medium text-text-primary">{dataset.name}</span>
            <span className="text-2xs text-text-secondary">
              {new Date(dataset.created_at).toLocaleDateString()}
            </span>
          </div>
          <p className="mt-1 text-2xs text-text-secondary">
            {dataset.rows.toLocaleString()} rows · {dataset.source_label}
            {dataset.symbols.length > 0 ? ` · ${dataset.symbols.join(", ")}` : ""}
          </p>
        </li>
      ))}
    </ul>
  );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run src/components/data/DatasetList.test.tsx`
Expected: PASS.

- [ ] **Step 5: Typecheck, lint, commit**

Run: `npx tsc --noEmit` and `npm run lint`

```bash
cat > /tmp/d2.txt <<'EOF'
feat(web): DatasetList shows a project's stored data

A presentational list of a project's datasets -- name, rows, source label,
symbols, date -- or an empty hint when there are none. Handed its data so
the container stays about the flow.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add frontend/src/components/data/DatasetList.tsx frontend/src/components/data/DatasetList.test.tsx
git commit -F /tmp/d2.txt && rm -f /tmp/d2.txt
```

---

## Task 3: `ProjectData` — the flow container

**Files:**
- Modify: `frontend/src/test/fakeApi.ts` (uploads/confirm routes + `makeUpload`)
- Create: `frontend/src/components/data/ProjectData.tsx`
- Test: `frontend/src/components/data/ProjectData.test.tsx`

**Interfaces:**
- Consumes: `api.listDatasets`, `api.uploadFile`, `api.confirmUpload`; `queryKeys.datasets`; `ColumnMapping`; `DatasetList`; `Upload`, `ColumnRole`.
- Produces: `ProjectData({ projectId }: { projectId: string })`.

- [ ] **Step 1: Extend the fetch stub**

Add to `frontend/src/test/fakeApi.ts`:

- import `Upload`:
  ```ts
  import type { Chat, Dataset, Project, Upload } from "../lib/types";
  ```
- add `uploads: Upload[]` to the `FakeApi` interface and `uploads: []` to `state`;
- the helper:
  ```ts
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
  ```
- the routes (before the final 404):
  ```ts
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
    return json(200, { ...upload, confirmed: true, observations: 100, symbols: ["AAA"], fields: ["price"] });
  }
  ```

- [ ] **Step 2: Write the failing test**

Create `frontend/src/components/data/ProjectData.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { installFakeApi, makeDataset, makeProject, type FakeApi } from "../../test/fakeApi";
import { renderWithProviders } from "../../test/renderWithProviders";
import { ProjectData } from "./ProjectData";

let fake: FakeApi;

beforeEach(() => {
  vi.unstubAllGlobals();
  fake = installFakeApi();
});

const csv = () => new File(["date,AAA\n2020-01-01,100\n"], "prices.csv", { type: "text/csv" });

describe("ProjectData", () => {
  it("lists a project's datasets", async () => {
    const project = makeProject();
    fake.projects.push(project);
    fake.datasets.push(makeDataset(project.id, { name: "prices.csv" }));

    renderWithProviders(<ProjectData projectId={project.id} />);

    expect(await screen.findByText("prices.csv")).toBeInTheDocument();
  });

  it("shows the mapping screen after a file is chosen, then returns to the list", async () => {
    const project = makeProject();
    fake.projects.push(project);
    const { user } = renderWithProviders(<ProjectData projectId={project.id} />);

    await screen.findByText(/No data yet/);
    await user.upload(screen.getByLabelText("Upload file"), csv());

    // The mapping table appears (its confirm button is unique to it).
    expect(await screen.findByRole("button", { name: /confirm mapping/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /confirm mapping/i }));

    // Back to the list, now showing the ingested dataset.
    await waitFor(() =>
      expect(screen.getByLabelText("Datasets")).toBeInTheDocument(),
    );
    expect(fake.callsTo("POST", "/confirm")).toHaveLength(1);
  });

  it("surfaces an upload error and stays on the list", async () => {
    const project = makeProject();
    fake.projects.push(project);
    // Make the uploads route reject, like a 415 unsupported type.
    fake.fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const path = typeof input === "string" ? input : input.toString();
      if (path.endsWith("/datasets")) return new Response(JSON.stringify([]), { status: 200 });
      if (path.endsWith("/uploads")) {
        return new Response(JSON.stringify({ detail: "cannot index .xlsx" }), { status: 415 });
      }
      return new Response(JSON.stringify([]), { status: 200 });
    });
    const { user } = renderWithProviders(<ProjectData projectId={project.id} />);

    await screen.findByText(/No data yet/);
    await user.upload(screen.getByLabelText("Upload file"), csv());

    expect(await screen.findByRole("alert")).toHaveTextContent("cannot index");
    expect(screen.queryByRole("button", { name: /confirm mapping/i })).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run to verify it fails**

Run: `npx vitest run src/components/data/ProjectData.test.tsx`
Expected: FAIL — cannot resolve `./ProjectData`.

- [ ] **Step 4: Implement**

Create `frontend/src/components/data/ProjectData.tsx`:

```tsx
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ChangeEvent, useRef, useState } from "react";

import { api } from "../../lib/api";
import { queryKeys } from "../../lib/queryClient";
import type { ColumnRole, Upload } from "../../lib/types";
import { Button } from "../ui/Button";
import { ColumnMapping } from "../uploads/ColumnMapping";
import { DatasetList } from "./DatasetList";

/**
 * A project's Data view: its stored datasets, and the upload → map → confirm
 * flow. Rendered in the canvas slot when a project (but no chat) is selected.
 *
 * One piece of local state carries the whole flow: `upload` is `null` in list
 * mode and an `Upload` while its columns are being mapped. Nothing is stored
 * until Confirm — the human-in-the-loop the backend also enforces.
 */
export function ProjectData({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [upload, setUpload] = useState<Upload | null>(null);

  const datasets = useQuery({
    queryKey: queryKeys.datasets(projectId),
    queryFn: () => api.listDatasets(projectId),
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => api.uploadFile(projectId, file),
    onSuccess: (result) => setUpload(result),
  });

  const confirmMutation = useMutation({
    mutationFn: (vars: { uploadId: string; roles: Record<string, ColumnRole> }) =>
      api.confirmUpload(vars.uploadId, vars.roles),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.datasets(projectId) });
      setUpload(null);
    },
  });

  function pick(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    // Reset so choosing the same file again after a cancel still fires change.
    event.target.value = "";
    if (file) uploadMutation.mutate(file);
  }

  if (upload) {
    return (
      <div className="flex flex-col items-start gap-4 p-4">
        <ColumnMapping
          upload={upload}
          busy={confirmMutation.isPending}
          error={confirmMutation.isError ? (confirmMutation.error as Error).message : null}
          onConfirm={(roles) => confirmMutation.mutate({ uploadId: upload.id, roles })}
        />
        <Button
          variant="ghost"
          onClick={() => setUpload(null)}
          disabled={confirmMutation.isPending}
        >
          Cancel
        </Button>
      </div>
    );
  }

  return (
    <section aria-label="Project data" className="flex flex-col gap-4 p-4">
      <header className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-medium text-text-primary">Data</h2>
        <Button
          variant="primary"
          onClick={() => fileInput.current?.click()}
          disabled={uploadMutation.isPending}
        >
          {uploadMutation.isPending ? "Uploading…" : "Upload data"}
        </Button>
        <input
          ref={fileInput}
          type="file"
          aria-label="Upload file"
          accept=".csv,.tsv,.txt,.xlsx"
          className="sr-only"
          onChange={pick}
        />
      </header>

      {uploadMutation.isError && (
        <p role="alert" className="text-2xs text-negative">
          {(uploadMutation.error as Error).message}
        </p>
      )}

      {datasets.isPending ? (
        <p className="text-2xs text-text-secondary">Loading…</p>
      ) : (
        <DatasetList datasets={datasets.data ?? []} />
      )}
    </section>
  );
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `npx vitest run src/components/data/ProjectData.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 6: Typecheck, lint, commit**

Run: `npx tsc --noEmit` and `npm run lint`

```bash
cat > /tmp/d3.txt <<'EOF'
feat(web): ProjectData drives upload, map and confirm

The flow container for a project's Data view: it lists the datasets, and
on upload swaps to the ColumnMapping screen, confirming through
confirmUpload and refetching so the new dataset appears. One piece of state
(upload: Upload | null) carries list-vs-mapping; nothing is stored until
Confirm. Upload errors surface and keep the app on the list. The fetch stub
grows uploads/confirm routes and a makeUpload fixture.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add frontend/src/components/data/ProjectData.tsx frontend/src/components/data/ProjectData.test.tsx \
  frontend/src/test/fakeApi.ts
git commit -F /tmp/d3.txt && rm -f /tmp/d3.txt
```

---

## Task 4: Wire it into the app, and look at it

**Files:**
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `ProjectData` (Task 3); `useSelectionStore` (existing).

- [ ] **Step 1: Wire the canvas slot**

In `frontend/src/App.tsx`, add the import:
```tsx
import { ProjectData } from "./components/data/ProjectData";
```

Change the `canvas` prop of `<AppShell>` to a three-way choice:
```tsx
      canvas={
        selectedChatId && selectedProjectId ? (
          <RunPanel
            key={selectedChatId}
            chatId={selectedChatId}
            projectId={selectedProjectId}
          />
        ) : selectedProjectId ? (
          // A project with no chat selected shows its Data, in the empty state
          // the canvas would otherwise fall to.
          <ProjectData key={selectedProjectId} projectId={selectedProjectId} />
        ) : undefined
      }
```

- [ ] **Step 2: Full frontend gate**

Run, from `frontend/`:
```bash
npx vitest run
npx tsc --noEmit
npm run lint
```
Expected: all green — the whole suite (existing + `DatasetList`, `ProjectData`), no type errors, no lint errors.

- [ ] **Step 3: Look at the app**

Bring up the stack and drive it:

- `./start.ps1` from the repo root (DB, migrations, API on 8001, web on 5173), or the preview tools against the dev server.
- Create or open a project, select it (not a chat) → the centre pane shows **Data** with "No data yet".
- Click **Upload data**, choose a real two-column CSV (a `date` column and a numeric ticker column) → the **column-mapping table** appears with roles pre-filled and reasons shown.
- Confirm → the mapping disappears and the **dataset row appears** in the list with its row count and symbol.
- Take a screenshot of the mapping screen and of the populated list. This is the evidence the screen works end to end, against the real backend, not a stub.

If anything is off (a role mis-mapped, the list not refreshing, a layout break in the narrow-ish canvas), fix it and re-run the gate before committing.

- [ ] **Step 4: Commit**

```bash
cat > /tmp/d4.txt <<'EOF'
feat(web): show a project's Data in the canvas on project select

App wires ProjectData into the canvas slot: a chat shows its runs, a
project with no chat shows its Data -- the upload/map/confirm flow and the
project's datasets -- in the empty state the canvas already fell to. No
change to the three-pane shell. Verified in the browser: uploaded a real
CSV, mapped its columns, confirmed, and the dataset appeared.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add frontend/src/App.tsx
git commit -F /tmp/d4.txt && rm -f /tmp/d4.txt
```

---

## Final verification

From `frontend/`:

- [ ] `npx vitest run` — whole suite green (existing + the new `api`, `DatasetList`, `ProjectData` tests).
- [ ] `npx tsc --noEmit` — clean.
- [ ] `npm run lint` — clean.
- [ ] The browser check in Task 4 done, with screenshots.

Optionally, from `frontend/`, `npm run test:e2e` — the design does not add an e2e spec (server-side upload paths are already gated, and the gap this closes is the screen), so the existing e2e suite should remain green, not grow.

---

## Self-review notes

- **Spec coverage.** Placement in the canvas slot on project-select (Task 4) ↔ design "Placement". `ProjectData` container with the list/mapping state machine and mutations (Task 3) ↔ design "Components / Data flow". `DatasetList` presentational + empty hint (Task 2) ↔ design "DatasetList". `Dataset` type + `listDatasets` (Task 1) ↔ design "API and types". `fakeApi` extension + the three test files ↔ design "Testing"; the browser check ↔ design "Then look at the app". `ColumnMapping` reused unchanged, gallery untouched, shell untouched — all as the design's "What this does not do" states.
- **Type consistency.** `Dataset` fields match `DatasetRead`. `api.listDatasets(projectId) -> Promise<Dataset[]>`; `queryKeys.datasets(projectId)`; `ProjectData({ projectId })`; `DatasetList({ datasets })`. `ColumnMapping`'s props (`upload`, `onConfirm(roles)`, `busy`, `error`) match its existing signature. `confirmMutation` vars `{ uploadId, roles }` match `api.confirmUpload(uploadId, roles)`.
- **No placeholders.** Every code and test block is complete. The one cross-task subtlety — `fakeApi.ts` is edited in both Task 1 (datasets slice) and Task 3 (uploads slice) — is called out explicitly in Task 1's ordering note, with the exact split, rather than left ambiguous.
- **Ordering.** Task 1 adds the datasets state/route/helper to `fakeApi` so its own test runs; Task 3 adds the uploads state/routes/helper. Both edits are additive and do not collide.
