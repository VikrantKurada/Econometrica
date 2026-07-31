"""Retrieved passages, in the shape the trace and the prompt both want.

Deliberately free of `db.models`. The orchestrator has to call retrieval before
planning and record it as a trace step, but `agents/` must never import the
database layer — the same rule that makes `search()` take a bare `enabled: bool`.
So the protocol the orchestrator sees, and the outcome it records, live here; the
concrete `ProjectRetriever` that touches the database lives in `services/rag.py`,
behind this protocol.

The mirror of `tools/web_search.SearchOutcome`, and for the same reasons: every
retrieval is an attributed trace step, and nothing it returns is a result — the
grounding gate admits only what a tool computed.
"""

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from econometrica.agents.trace import StepRecord

#: Kept on the trace step. The query and model are the audit; the passages
#: themselves are in the prompt where they were used.
_DETAIL_LIMIT = 300


@dataclass(frozen=True)
class Retrieved:
    """One passage, and where it came from."""

    document_id: UUID
    document_name: str
    ordinal: int
    text: str
    #: 0 to 1, from cosine distance. Comparable within one query only.
    score: float


@dataclass(frozen=True)
class RetrievalOutcome:
    """One retrieval, for the trace and the prompt."""

    model: str
    query: str
    hits: list[Retrieved] = field(default_factory=list)
    failed: bool = False
    detail: str = ""

    def as_context(self) -> str:
        """The passages as prompt text, each attributed.

        The header marks the text as *read* rather than *computed*, the same
        wording web search uses. It is for the model; the grounding gate is what
        actually enforces it.
        """
        if not self.hits:
            return ""
        blocks = [f"[{hit.document_name} #{hit.ordinal}]\n{hit.text}" for hit in self.hits]
        return (
            "# Retrieved context — read from documents, not computed.\n"
            "Nothing here is a result. Do not cite a number from it.\n\n"
            + "\n\n".join(blocks)
        )

    def to_step_record(self) -> StepRecord:
        return StepRecord(
            agent="planner",
            kind="tool",
            status="failed" if self.failed else "ok",
            tool=f"retrieval:{self.model}",
            detail=(
                f"{self.query} — {self.detail}"
                if self.failed
                else f"{self.query} — {len(self.hits)} passage(s)"
            )[:_DETAIL_LIMIT],
        )


class Retriever(Protocol):
    """What the orchestrator sees. The concrete implementation holds the session
    and project; this hides both, so `agents/` stays free of `db.models`."""

    model: str

    async def fetch(self, query: str) -> RetrievalOutcome:
        ...
