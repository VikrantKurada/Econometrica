# Econometrica — document retrieval over pgvector

*Design note, 2026-07-31. Read `CLAUDE.md` first; this closes the retrieval half
it records as missing — "there is no route to add a document, so a project has
nothing to retrieve from", and `services/rag.py` "still imported only by its own
tests".*

---

## What is already here, and what is not

The retrieval *machinery* is built and tested; only the wiring that would let a
user reach it is missing.

**Present.**

- `services/rag.py` — `chunk_text` (sentence-boundary chunks with overlap),
  `ingest_document` (chunk → embed → store), `retrieve` (project-scoped cosine
  search, filtered to the chunk's own embedding model), `as_context`, the
  `Embedder` protocol, the `Retrieved` dataclass, `_padded`.
- `db/models/document.py` — `Document` and `DocumentChunk`, with `project_id`
  denormalised onto the chunk as the scope filter and `embedding_model` recorded
  per chunk. Migration `7441efabf4a6` already creates both tables.
- `llm/embeddings.py` — `OllamaEmbedder` (`POST /api/embed`, `all-minilm`,
  384 dimensions).

**Missing — confirmed:** `retrieve`, `ingest_document` and `OllamaEmbedder` are
imported nowhere in `src` outside their own modules.

1. **No route to add, list or remove a document.** A project has nothing to
   retrieve from.
2. **No embedder dependency.** Nothing in the app constructs an `OllamaEmbedder`.
3. **Retrieval never reaches a run.** The orchestrator has no retrieval step;
   documents, even if they existed, would never inform a plan.
4. **No `to_step_record` for a retrieval.** A retrieval that ran would leave no
   attributed trace step, which §9 of the design requires.

---

## The one hard constraint that shapes everything

**`agents/` must not import `db.models`.** CLAUDE.md records this as a scar:
`search()` takes `enabled: bool` rather than a `ResolvedCapabilities` precisely
because "reading one flag off a services type put `db.models` on the import path
of everything touching a search — which now includes `agents/`."

The orchestrator has to *call* retrieval before planning and record it as a trace
step. But `rag.retrieve` needs a `session` and a `project_id` — both `db`/project
concepts the orchestrator must not know, and `rag.py` imports `db.models`
directly. So retrieval needs the **same protocol seam web search has**: the
orchestrator sees a narrow protocol, and a concrete implementation behind it
holds the session and project.

The seam is *almost* web search's, with one difference. Web search's concrete
provider (DuckDuckGo) is itself db-free, so protocol and provider both live in
`tools/web_search.py`. Retrieval's concrete provider must touch the database, so
it splits across two layers:

- **`tools/retrieval.py` (new, db-free).** Imports only `agents.trace.StepRecord`.
  Holds:
  - `Retrieved` — one passage (moved here from `rag.py`).
  - `RetrievalOutcome` — one retrieval, in the shape the trace and the prompt
    both want: `{query, model, hits, failed, detail}`, with `to_step_record()`
    and `as_context()`. The mirror of `web_search.SearchOutcome`.
  - `Retriever` — a `Protocol` with `model: str` and
    `async def fetch(self, query: str, *, limit: int = ...) -> RetrievalOutcome`.
- **`services/rag.py` (may import `db.models`).** Keeps `chunk_text`,
  `ingest_document`, `retrieve`, `_padded`; imports `Retrieved`/`RetrievalOutcome`
  back from `tools/retrieval.py`. Gains `ProjectRetriever`, the concrete
  `Retriever`: it binds a `session`, a `project_id` and an `Embedder`, and its
  `fetch` calls `retrieve(...)`, wrapping the passages in a `RetrievalOutcome` and
  turning an `EmbeddingError` into a *failed* outcome rather than a raise.

`as_context` moves onto `RetrievalOutcome` (it is currently a free function in
`rag.py`); `rag.py`'s callers and tests import it from its new home. The move is
mechanical, but it is the point of the seam — the prompt-and-trace shaping lives
in the db-free module, so the orchestrator never reaches through it to the
database.

```
agents/orchestrator.py ──imports──▶ tools/retrieval.py   (Retriever, RetrievalOutcome, Retrieved)
                                          ▲
api/routers/documents.py ─builds─▶ services/rag.py.ProjectRetriever ──implements──┘
        │                                 │
        └───────────── db.models ◀────────┘
```

---

## Ingest — three routes

A new `api/routers/documents.py`, mounted like `uploads.py`.

### `POST /api/projects/{id}/documents` — add a document

Multipart file upload, mirroring `create_upload`. The flow:

1. `get_project_or_404`.
2. Read the bytes under a size cap (`MAX_DOCUMENT_BYTES`, a few MB — a retrieval
   corpus is prose, not data).
3. **Extract text** — `services/documents.py::extract_text(filename, data)`:
   - `.txt`, `.md`, `.markdown` → `data.decode("utf-8", errors="replace")`.
   - `.pdf` → `pypdf`, text per page joined by blank lines.
   - anything else → `DocumentError`, mapped to **415 Unsupported Media Type**
     naming the supported set.
   - empty result → `DocumentError`, mapped to **400**.
4. **Dedupe.** Compute the text fingerprint (`ingest_document` already does, via
   `sha256`); if a `Document` with that fingerprint already exists in the
   project, **409** rather than a second copy — duplicate chunks crowd retrieval
   with near-identical passages.
5. `rag.ingest_document(session, project_id, name, text, embedder)`.
6. **`await session.commit()`.** This is the exact scar CLAUDE.md records: "`get_
   session` does not commit and the route did not either, so the whole ingest was
   discarded while the response reported what it would have stored." The commit is
   not optional and its test must prove it (below).
7. Return `DocumentRead`.

Embedding failure (Ollama down) surfaces as **502** with the embedder's reason —
you cannot store a document without vectors, so ingest fails loudly rather than
degrading. This is the opposite of a *run's* retrieval, which degrades; the
difference is that ingest has nothing useful to fall back to.

### `GET /api/projects/{id}/documents` — list

Returns `list[DocumentRead]` — `{id, name, chars, chunks_count, created_at}` —
so a UI (and the tests) can show what a project has indexed. Ordered newest
first.

### `DELETE /api/documents/{id}` — remove

**204.** The chunks go with it: `Document.chunks` cascades
(`all, delete-orphan`, `passive_deletes=True`) and the FK is `ON DELETE CASCADE`.
Scoped by document id alone, like `GET /api/runs/{id}` — a document id is enough
and the deleting UI holds one.

`schemas/document.py` holds `DocumentRead`. The embedder is a new dependency in
`api/deps.py`:

```python
def get_embedder() -> Embedder:
    return OllamaEmbedder()

EmbedderDep = Annotated[Embedder, Depends(get_embedder)]
```

`all-minilm` at 384 dimensions — the width `document_chunks.embedding` is
declared at. Not a setting yet; a wider model needs a migration, not a flag, and
YAGNI until a second model is actually wanted.

---

## Run wiring

The orchestrator gains one constructor parameter, `retriever: Retriever | None =
None`, passed in for the same reason `searcher` and `query_writer` are:
`agents/` decides nothing about projects, and the router supplies a retriever
only when there is something to retrieve.

A new `_retrieval_context(question, context, trace)`, sibling to
`_search_context` and called just before it in `_pipeline`:

```python
if self.retriever is None:
    return context
outcome = await self.retriever.fetch(question)
record = outcome.to_step_record()
record.prompt = question[:PROMPT_LIMIT]
record.response = outcome.as_context()[:PROMPT_LIMIT]
trace.add(record)
found = outcome.as_context()
return f"{context}\n\n{found}" if (context and found) else (found or context)
```

Three properties, each inherited from web search rather than invented:

- **Planner only, never the Narrator.** Retrieved passages are dense with
  numbers, the Narrator's output is what the grounding gate judges, and the gate
  withholds a whole narration over one number it cannot match. Same mechanism,
  same reason.
- **The step is `agent="planner"`, `tool="retrieval:<model>"`.** The retrieval
  feeds the planner, exactly as the search step does — so no new trace-agent
  vocabulary and **no CHECK migration**. The one thing this feature does *not*
  need that the query writer did.
- **Failure degrades the run.** `ProjectRetriever.fetch` turns an `EmbeddingError`
  (Ollama down mid-run) into a failed `RetrievalOutcome`; the orchestrator records
  the failed step and appends nothing. A run with less context is better than no
  run.

### The gate: documents-presence, no toggle

There is no `retrieval_enabled` capability. Uploading a document to a project
*is* the opt-in — a document reaches only the project it was added to, already
scoped, unlike web search which reaches the whole internet and needs an explicit
switch.

`api/routers/runs.py::_build` runs a cheap `COUNT` of the project's chunks and
constructs a `ProjectRetriever(session, project_id, embedder)` **only when it is
non-zero**. No documents → `retriever=None` → the orchestrator does no embed and
records no step. This keeps a run on a project that never used retrieval exactly
as it is today.

Re-run is untouched: it re-executes a recorded plan without re-planning, and
retrieval only ever shaped the plan's *context*, never its results.

---

## Testing, red first

Strict TDD — each test written, run, and watched fail before the implementation.

**`tests/services/test_documents.py`** (extraction):

- `.txt` and `.md` decode to their text;
- a real one-page PDF (built in-test with `pypdf`) extracts its text;
- an unsupported suffix raises `DocumentError`;
- a file that extracts to whitespace raises `DocumentError`.

**`tests/tools/test_retrieval.py`** (`RetrievalOutcome`, db-free):

- `to_step_record()` is a `retrieval:<model>` tool step under `agent="planner"`,
  `ok` with the passage count, `failed` with the detail;
- `as_context()` attributes every passage and carries the read-not-computed
  header;
- a failed or empty outcome yields no context.

**`tests/api/test_documents.py`** (the routes):

- add a `.txt`, then `GET` lists it with the right `chunks_count`;
- **the commit test**: read the document back through a *separate* session, not
  the request's — the shared-session client "cannot tell a flush from a write",
  so only a second session proves the route committed;
- a second add of the same bytes → **409**;
- an unsupported type → **415**; an empty document → **400**;
- with the embedder stubbed to raise, add → **502** and nothing is stored;
- `DELETE` removes the document and its chunks (count the chunks after).

**`tests/agents/test_orchestrator.py`** (run wiring), mirroring the web-search
tests:

- a retriever's passages reach the Planner's prompt and leave a `retrieval:` step;
- with `retriever=None`, no retrieval step and no embed;
- a retriever that fails degrades the run (status completed, failed step, no
  context);
- the retrieval step precedes the planner turn.

**`tests/api/test_runs.py`**: a project with an indexed document retrieves in a
run (a `ProjectRetriever` is built); a project with none does not (asserted on
construction, like the search-provider test).

**The grounding invariant stays.** `tests/services/test_rag.py` already asserts a
figure quoted verbatim from a retrieved passage is still blocked by the grounding
gate. It moves with `as_context` but the assertion does not change — retrieval
must not become a side channel into the one mechanical anti-hallucination check
the system has.

**Live probe** (`@pytest.mark.live`): against a real Ollama, ingest a short
document, retrieve a query that matches one passage, and assert that passage
ranks first. Skips when Ollama is absent, like the other live tests. This is the
proof the embedder and pgvector actually agree end to end, not just that the
adapters match what we believe the wire format is.

---

## What this does not do

- **No UI.** The document-management screen is a separate, frontend concern, and
  uploads set the precedent: the backend is built first, the three-pane screen
  follows. A run consults a project's documents the moment they are ingested,
  screen or no screen.
- **No OCR and no `.docx`.** Scanned PDFs (image-only) extract to nothing and are
  refused as empty; `.docx` is an unsupported type. Both are input-adapter
  additions that do not change the seam.
- **No retrieval toggle.** Documents-presence is the gate. A toggle would cost a
  Project column, a migration, and a capability field, for control the delete
  route already gives.
- **No second embedding model.** 384-dim `all-minilm` is the schema's width. A
  wider model is refused by `_padded` rather than truncated, and adding one is a
  migration — out of scope here.
