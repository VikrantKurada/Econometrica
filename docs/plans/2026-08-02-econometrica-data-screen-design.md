# Econometrica — the Data screen: uploads in the app

*Design note, 2026-08-02. Read `CLAUDE.md` first; this closes the item it records
as "the `ColumnMapping` screen is still only in `/gallery.html` … what remains is
deciding where 'Data' lives in the three-pane layout."*

---

## What is already here, and what is not

The upload *mechanism* is complete on both sides; only the screen that drives it
in the app is missing.

**Present.**

- `components/uploads/ColumnMapping.tsx` — the mapping screen: every column's role
  editable, the profiler's reason shown, ambiguous columns flagged, client-side
  validation mirroring `confirm_mapping`, and a Confirm button disabled until the
  mapping is usable. It takes an `Upload` and an `onConfirm(roles)` callback, plus
  `busy`/`error`. It is rendered today only as a static fixture in `gallery.tsx`.
- `lib/api.ts` — `uploadFile(projectId, file)`, `readUpload(id)`,
  `confirmUpload(id, roles)`. The multipart upload is already there.
- Backend — `POST /api/projects/{id}/uploads` (profile + proposal),
  `POST /api/uploads/{id}/confirm`, and `GET /api/projects/{id}/datasets`
  (`list[DatasetRead]`), all working.

**Missing.**

1. **A home for "Data" in the app.** The shell (`AppShell`) is hard-wired to three
   panes — `projects | canvas | chat` — and nothing renders the upload flow.
2. **The flow that ties the pieces together** — pick a file → upload → map →
   confirm → see the stored dataset.
3. **A datasets list** — the frontend has no `Dataset` type and no
   `api.listDatasets`, so a project's stored data cannot be shown.

---

## Placement: the centre pane, on project select

Selecting a project row already clears the chat selection
(`selectProject` sets `selectedChatId = null`), so the centre pane currently falls
to an empty "No artifacts yet" state whenever a project — but no chat — is
selected. That unused state is where Data goes.

`App.tsx`'s canvas slot gains a middle case:

```tsx
canvas={
  selectedChatId && selectedProjectId ? (
    <RunPanel key={selectedChatId} chatId={selectedChatId} projectId={selectedProjectId} />
  ) : selectedProjectId ? (
    <ProjectData key={selectedProjectId} projectId={selectedProjectId} />
  ) : undefined
}
```

The selection model reads cleanly: **a project shows its data; a chat shows its
runs.** No new pane, no change to the layout store, `PANE_ORDER`, the toggles, or
their tests. `ProjectData` renders inside `CanvasPane` (under its "Canvas" header)
exactly as `RunPanel` does, and is keyed on `projectId` so switching projects
resets the flow rather than carrying a half-finished mapping across.

The one trade-off: opening a chat replaces the Data view with that chat's runs.
That is the intended workflow — upload data on the project, then open a chat to
analyse it — and a click back on the project name returns to Data.

---

## Components

Three units, each with one job.

### `ProjectData` (new) — the container

Owns the flow and nothing visual beyond arranging its two states. It holds:

- a React-Query `["datasets", projectId]` reading `api.listDatasets(projectId)`;
- one piece of local state, `upload: Upload | null` — `null` is list mode, a value
  is mapping mode;
- an upload mutation (`api.uploadFile`) whose success sets `upload`;
- a confirm mutation (`api.confirmUpload`) whose success invalidates the datasets
  query and clears `upload`.

Render:

- **list mode** (`upload === null`) — a header with an **Upload data** button
  (a hidden `<input type="file">` it triggers), the upload error if the last
  attempt failed, and `<DatasetList>`.
- **mapping mode** — `<ColumnMapping upload={upload} onConfirm={confirm}
  busy={confirmMutation.isPending} error={confirmError} />`, with a **Cancel**
  that sets `upload` back to `null`.

The accepted file types are the ones the profiler reads — `.csv`, `.tsv`,
`.txt`, `.xlsx` — set on the input's `accept` so the picker filters, though the
backend remains the authority.

### `DatasetList` (new) — presentational

Given `datasets: Dataset[]`, renders one row each — name, source label, row count,
symbols, and date — or a one-line empty hint ("No data yet. Upload a file to get
started."). No network, no state; it is handed its data. This keeps the list
independently testable and `ProjectData` focused on the flow.

### `ColumnMapping` (exists) — reused unchanged

No change. It already takes `busy`/`error` and validates before enabling Confirm.

---

## Data flow

```
[list]  --Upload data--> file input --onChange--> uploadFile(file)
                                                       |
                                        success: setUpload(Upload)  --> [mapping]
                                        error:   show upload error   --> [list]

[mapping]  --Confirm--> confirmUpload(uploadId, roles)
                                                       |
                              success: invalidate ["datasets", id]; setUpload(null) --> [list, new dataset shown]
                              error:   pass to ColumnMapping.error                    --> [mapping]

[mapping]  --Cancel--> setUpload(null)  --> [list]
```

`confirmUpload` returns the stored dataset's identity, but the container does not
need it: invalidating the datasets query and returning to the list makes the new
row appear, which is the feedback that the ingest landed. Errors are already
formatted by `ApiError.message` (the shared `formatApiError`), so both surfaces
render a sentence a person can act on — a 415 unsupported type, a 422 bad
mapping, a 502.

---

## API and types

- `lib/types.ts` — a `Dataset` interface mirroring `DatasetRead`:

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

- `lib/api.ts` — `listDatasets(projectId: string): Promise<Dataset[]>` →
  `GET /projects/${projectId}/datasets`.

---

## Testing

The component tests stub `fetch` through the shared `test/fakeApi.ts`, which today
knows only projects and chats. Extend it:

- state: `datasets: Dataset[]`; helpers `makeDataset` and `makeUpload` (an `Upload`
  fixture with a small profile and proposal);
- routes: `GET /api/projects/{id}/datasets` (filter by project),
  `POST /api/projects/{id}/uploads` (return `makeUpload`; the body is FormData, so
  it is matched on path+method, not parsed), and `POST /api/uploads/{id}/confirm`
  (return a confirmed `Upload` **and** push a `Dataset` so the refetch shows it).

Then:

- **`DatasetList.test.tsx`** — renders the rows a project has; shows the empty hint
  when it has none.
- **`ProjectData.test.tsx`** — lists a project's datasets; **Upload data** →
  choosing a file shows the mapping table; **Confirm** returns to the list with the
  new dataset present (proving the query was invalidated); an upload error (a
  stubbed 415) surfaces above the button and the app stays in list mode; **Cancel**
  from a mapping returns to the list.

`ColumnMapping.test.tsx` is unchanged.

**Then look at the app.** The standing rule — the one that caught a `mixed_sources`
flag that every test missed — applies here: run the dev stack, select a project,
upload a real CSV, map its columns, confirm, and confirm the dataset appears. A
screenshot is the evidence. An e2e spec is **not** added in this pass: it needs a
live backend on 8100/8101 and the existing gates already cover the upload
endpoints server-side; the gap this closes is the screen, verified by looking at
it.

---

## What this does not do

- **Delete or re-map a stored dataset.** There is no backend route for either, and
  adding one is a separate change. A dataset is added and listed; removing it is
  out of scope.
- **Touch the three-pane shell.** No fourth pane, no store or toggle changes — the
  whole feature lives in the canvas slot's project branch.
- **Replace the gallery fixture.** `gallery.tsx` keeps its static `ColumnMapping`
  preview; it is a design harness and costs nothing.
