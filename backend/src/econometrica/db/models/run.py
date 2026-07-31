"""One assistant turn's worth of agent work, and the steps inside it.

A `Run` is what happened when a question was asked; a `Step` is one unit of
work inside it — a model call or a tool invocation — linked to the step it
followed. Together they are the trace the canvas renders and the cost
dashboard sums, which is why the columns lean towards accounting rather than
narrative.

Two decisions that cost real time when got wrong, both already learned on
`Message`:

* Steps order on ``seq``, a Postgres identity column. ``created_at`` is the
  *transaction* timestamp, so every step of a run written in one transaction
  ties exactly and a trace sorted on it comes back scrambled.
* Every NOT NULL column pairs a Python ``default`` with a ``server_default``,
  so a non-ORM insert still lands a valid row.

Alembic autogenerates none of the CHECK constraints below and ``alembic check``
cannot verify them either. ``tests/db/test_run_model.py`` is their only gate.
"""

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Float,
    ForeignKey,
    Identity,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from econometrica.db.base import TimestampedBase

if TYPE_CHECKING:
    from econometrica.db.models.chat import Chat

#: `running` exists so a run is recordable *before* it finishes. A crash then
#: leaves a row saying a run started and never ended, which is information; a
#: run written only on success leaves nothing at all.
RUN_STATUSES = ("running", "completed", "blocked", "failed")
RUN_TIERS = ("single", "critic", "consensus")

STEP_AGENTS = (
    "planner",
    "data_steward",
    "econometrician",
    "validator",
    "narrator",
    "quant_coder",
    "query_writer",
)
STEP_KINDS = ("llm", "tool")
STEP_STATUSES = ("ok", "refused", "failed", "skipped")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class Run(TimestampedBase):
    """One question, from prompt to published interpretation or refusal."""

    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(_in_list("status", RUN_STATUSES), name="ck_runs_status_known"),
        CheckConstraint(_in_list("tier", RUN_TIERS), name="ck_runs_tier_known"),
        CheckConstraint("length(trim(question)) > 0", name="ck_runs_question_not_blank"),
        CheckConstraint("revisions >= 0", name="ck_runs_revisions_non_negative"),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0"
            " AND cache_read_tokens >= 0 AND cache_write_tokens >= 0",
            name="ck_runs_tokens_non_negative",
        ),
        CheckConstraint("cost_usd >= 0", name="ck_runs_cost_non_negative"),
    )

    chat_id: Mapped[UUID] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), index=True
    )
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="running", server_default="running")
    tier: Mapped[str] = mapped_column(String(20), default="critic", server_default="critic")
    revisions: Mapped[int] = mapped_column(default=0, server_default="0")
    #: Populated when the pipeline stopped early. Empty on a clean run.
    error: Mapped[str] = mapped_column(Text, default="", server_default="")

    #: The whole `RunOutcome` as JSON — plan, data quality, results, charts,
    #: verdict and narration. The steps beside it say what the run *did*; this
    #: says what it *produced*, and without it a run is only readable while its
    #: SSE stream is open. JSONB rather than Text so a later phase can query
    #: into it (which tools a project actually uses) without a migration.
    #:
    #: A result's series are in here, so a row is tens to hundreds of KB. That
    #: is the price of a canvas that survives a reload, and it is why `RunRead`
    #: leaves the column out — listing runs must not drag every series with it.
    outcome: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )

    # Totals over the run's steps, denormalised so a dashboard listing many
    # runs does not aggregate the whole step table to draw one column.
    input_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    #: Zero until Phase 6 supplies per-model pricing; the column exists now so
    #: the trace does not need a migration to start costing anything.
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")

    chat: Mapped["Chat"] = relationship()
    steps: Mapped[list["Step"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="Step.seq",
    )


class Step(TimestampedBase):
    """One model call or tool invocation inside a run."""

    __tablename__ = "run_steps"
    __table_args__ = (
        CheckConstraint(_in_list("agent", STEP_AGENTS), name="ck_run_steps_agent_known"),
        CheckConstraint(_in_list("kind", STEP_KINDS), name="ck_run_steps_kind_known"),
        CheckConstraint(_in_list("status", STEP_STATUSES), name="ck_run_steps_status_known"),
        CheckConstraint("attempt >= 1", name="ck_run_steps_attempt_positive"),
        # A self-edge is not a DAG, and it makes a trace viewer loop forever.
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_run_steps_no_self_parent"),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0"
            " AND cache_read_tokens >= 0 AND cache_write_tokens >= 0",
            name="ck_run_steps_tokens_non_negative",
        ),
        CheckConstraint("cost_usd >= 0", name="ck_run_steps_cost_non_negative"),
    )

    #: Ordering key. See the module docstring: `created_at` ties inside a
    #: transaction, and a whole run is written in one.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True, index=True)

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    #: The step this one followed from. SET NULL rather than CASCADE: losing a
    #: parent should leave a hole in the trace, not delete the work that came
    #: after it.
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("run_steps.id", ondelete="SET NULL"), default=None, index=True
    )

    agent: Mapped[str] = mapped_column(String(30))
    kind: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10))
    #: Which try this was inside the agent's retry loop, from 1. A rejected
    #: attempt is its own step because it was billed as its own call.
    attempt: Mapped[int] = mapped_column(default=1, server_default="1")

    # Null on the deterministic roles, which run no model at all.
    provider: Mapped[str | None] = mapped_column(String(50), default=None)
    model: Mapped[str | None] = mapped_column(String(200), default=None)

    input_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")

    # Tool steps only.
    tool: Mapped[str | None] = mapped_column(String(100), default=None)
    #: Hash of the tool name and its parameters, so two runs can be compared
    #: for "did this compute the same thing" without storing the params twice.
    tool_call_hash: Mapped[str | None] = mapped_column(String(64), default=None, index=True)

    detail: Mapped[str] = mapped_column(Text, default="", server_default="")

    #: What this attempt was sent, and what came back. §8 asks a step to record
    #: them; without them a trace names the model but not the decision. Cut at
    #: `agents.base.PROMPT_LIMIT` before they get here.
    prompt: Mapped[str] = mapped_column(Text, default="", server_default="")
    response: Mapped[str] = mapped_column(Text, default="", server_default="")

    run: Mapped["Run"] = relationship(back_populates="steps")
