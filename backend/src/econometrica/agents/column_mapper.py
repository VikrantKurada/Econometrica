"""The Column Mapper: choosing among the roles a column could play.

The one genuinely model-shaped part of an upload, and deliberately the only
one. `services/ingest.py` decides what each column *could* be from its
contents; `services/mapping.py` takes the best of those and says which were a
real choice. This agent decides only those, and only among candidates the
profiler already scored as admissible.

Three things it therefore cannot do, each with a test: name a column the file
does not have, assign a role the profiler ruled out, or invent a role. All
three are rejected and retried with the problem named, through the same loop
`agents/base.py` gives every agent.

Two properties matter as much as the choosing:

* **It is skipped when there is nothing to decide.** A file whose every column
  has one obvious role is not a question, and asking anyway would put a billed
  turn behind every upload. `propose` returns without a call, and says so by
  returning no `AgentResult`.
* **It never produces a decision.** What comes back is a `MappingProposal`,
  which `apply_mapping` refuses. §9 of the design puts a person between the
  suggestion and the ingest, and that is not a step a good suggestion earns
  its way out of.
"""

from pydantic import BaseModel, Field

from econometrica.agents.base import Agent, AgentAttemptsExhaustedError, AgentResult
from econometrica.llm.base import LLMProvider
from econometrica.llm.types import Message
from econometrica.services.ingest import FileProfile, Role
from econometrica.services.mapping import MappingProposal, propose_mapping

_SYSTEM = """\
You are the Column Mapper in an econometrics workbench. A user has uploaded a
data file. Its columns have been profiled already, and every column that had
one obvious role has been settled without you.

What is left are the columns where more than one role genuinely fits. For each,
pick the role the data and the file most likely mean.

Rules:
- Choose only from the candidate roles listed for that column. They are what
  the column's contents can actually support; anything else will be rejected.
- Use only the column names listed. Do not invent one.
- Say nothing about columns that are not listed — they are already decided.
- A short reason is shown to the user beside your choice, so write it for them.

Reply with a single JSON object and nothing else:

{"columns": [{"column": "<name>", "role": "<one of its candidates>",
              "reason": "why, in one line"}]}\
"""


class ColumnChoice(BaseModel):
    column: str
    role: Role
    reason: str = ""


class MappingChoices(BaseModel):
    columns: list[ColumnChoice] = Field(default_factory=list)


class ColumnMapper(Agent[MappingChoices]):
    role = "column_mapper"

    def __init__(self, provider: LLMProvider, model: str, *, max_attempts: int = 2) -> None:
        super().__init__(provider, model, max_attempts=max_attempts)
        self._admissible: dict[str, set[str]] = {}

    def output_model(self) -> type[MappingChoices]:
        return MappingChoices

    def check(self, output: MappingChoices) -> None:
        problems: list[str] = []
        for choice in output.columns:
            allowed = self._admissible.get(choice.column)
            if allowed is None:
                problems.append(
                    f"there is no column {choice.column!r} to decide;"
                    f" the ones being asked about are {', '.join(sorted(self._admissible))}"
                )
            elif choice.role not in allowed:
                problems.append(
                    f"{choice.column!r} cannot be {choice.role!r} —"
                    f" its contents support only {', '.join(sorted(allowed))}"
                )
        if problems:
            raise ValueError("; ".join(problems))

    async def propose(
        self, profile: FileProfile
    ) -> tuple[MappingProposal, AgentResult[MappingChoices] | None]:
        """The profiler's proposal, with the real choices decided.

        Returns the proposal and the agent result, or ``None`` for the result
        where no model was consulted — which the caller needs in order to
        record the turn's cost, and which is the honest answer when there was
        no turn.
        """
        proposal = propose_mapping(profile)
        if proposal.unambiguous:
            return proposal, None

        self._admissible = {}
        for name in proposal.ambiguous:
            column = profile.column(name)
            if column is not None:
                self._admissible[name] = {c.role for c in column.candidates}

        try:
            result = await self.ask(
                [Message.system(_SYSTEM), Message.user(_render(profile, proposal))]
            )
        except AgentAttemptsExhaustedError:
            # An upload must not fail because a model would not answer. The
            # profiler's own proposal is still a reasonable suggestion, and the
            # user confirms it either way.
            return proposal, None

        roles = dict(proposal.roles)
        rationale = dict(proposal.rationale)
        for choice in result.output.columns:
            roles[choice.column] = choice.role
            if choice.reason:
                rationale[choice.column] = choice.reason

        return (
            MappingProposal(
                roles=roles, rationale=rationale, ambiguous=proposal.ambiguous
            ),
            result,
        )


def _render(profile: FileProfile, proposal: MappingProposal) -> str:
    """Only the undecided columns, and only what bears on deciding them."""
    lines = [
        f"File: {profile.filename} ({profile.format}, {profile.rows} rows,"
        f" {profile.layout} layout)",
        "",
        "Already decided: "
        + ", ".join(
            f"{name} = {role}"
            for name, role in proposal.roles.items()
            if name not in proposal.ambiguous
        ),
        "",
        "# Columns to decide",
    ]

    for name in proposal.ambiguous:
        column = profile.column(name)
        if column is None:
            continue
        lines.append(f"\n## {name}")
        lines.append(
            f"{column.dtype}, {column.present} values, {column.unique} distinct"
            + (
                f", from {column.minimum:g} to {column.maximum:g}"
                if column.minimum is not None and column.maximum is not None
                else ""
            )
        )
        if column.sample:
            lines.append("Examples: " + ", ".join(column.sample))
        lines.append(
            "Candidates: "
            + "; ".join(f"{c.role} ({c.reason})" for c in column.candidates)
        )

    return "\n".join(lines)
