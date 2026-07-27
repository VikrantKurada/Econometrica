"""The typed contract between agents.

Every field here exists to make one class of model error impossible to pass
downstream. A plan naming a tool that does not exist, carrying a parameter the
tool never declared, or describing a set of steps that cannot be ordered is
rejected at the boundary — not discovered when the econometrics core raises.

That is the whole reason these types are defined before any agent is: an
agent's retry loop is only worth having if something can tell it that the last
reply was wrong, and specifically enough that re-asking is likely to help.
"""

import json
import re
from collections.abc import Iterator
from datetime import date
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from econometrica.econ import load_tools
from econometrica.econ.registry import get_registry

# Resolving a tool by name needs a populated registry, and registration is an
# import side-effect of the family packages. Doing it here means no caller has
# to remember.
load_tools()

#: Fenced code blocks, with or without a language tag.
_FENCE = re.compile(r"```(?:[a-zA-Z]*)\s*\n?(.*?)```", re.DOTALL)


class AgentOutputError(ValueError):
    """A model reply that could not be read as the shape the agent asked for.

    Carries the raw text because the retry has to show the model what it
    actually sent. A retry that only says "that was not valid JSON" tends to
    get the same invalid JSON back.
    """

    def __init__(self, message: str, raw: str) -> None:
        super().__init__(message)
        self.raw = raw


def parse_agent_json(raw: str) -> dict[str, Any]:
    """Recover a JSON object from a model reply.

    Models wrap JSON in prose, in fences, or in both, and the ones with a JSON
    mode still do it when the mode is unavailable. Rather than forbid that,
    three strategies are tried in order of how much they assume: the whole
    reply, then any fenced block, then any balanced ``{...}`` span.

    A JSON *array* is not accepted. Every agent contract in this phase is an
    object, so a list is a misread of the question rather than a formatting
    quirk, and silently taking its first element would hide that.
    """
    for candidate in _candidates(raw):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise AgentOutputError("no JSON object found in the model reply", raw)


def _candidates(raw: str) -> Iterator[str]:
    yield raw.strip()
    for match in _FENCE.finditer(raw):
        yield match.group(1).strip()
    yield from _balanced_objects(raw)


def _balanced_objects(text: str) -> Iterator[str]:
    """Every complete ``{...}`` span, brace-counted outside string literals.

    A regex cannot do this: model prose routinely contains braces inside
    strings, and a non-greedy match stops at the first one.
    """
    depth = 0
    start = -1
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                yield text[start : index + 1]


#: Spellings of a return method that mean exactly what this schema means.
#: The catalogue a Planner reads uses the *tool-level* transform vocabulary —
#: "log_diff" for log returns — and a real local model reached for that
#: spelling here on its first attempt every time, burning a retry on a
#: synonym. A log difference is a log return; recognising that is not leniency
#: about meaning. "diff" is deliberately absent: a price difference is not a
#: simple return.
_RETURN_METHOD_SYNONYMS = {"log_diff": "log", "log_return": "log", "pct_change": "simple"}


class DatasetSpec(BaseModel):
    """What data an analysis runs on, before any of it has been fetched."""

    tickers: list[str] = Field(min_length=1)
    start: date
    end: date
    frequency: Literal["D", "W", "M", "Q", "A"] = "D"
    return_method: Literal["simple", "log"] = "log"

    @field_validator("return_method", mode="before")
    @classmethod
    def accept_tool_vocabulary(cls, value: object) -> object:
        if isinstance(value, str):
            return _RETURN_METHOD_SYNONYMS.get(value, value)
        return value
    #: Ticker or series id for the risk-free rate; None where the analysis
    #: does not need excess returns.
    risk_free: str | None = None
    #: Named factor set to join onto the frame, for the factor models. None
    #: where the analysis is not a factor study. Naming one supplies its own
    #: risk-free column too, because these factors are excess returns against
    #: their own RF.
    factors: Literal["ff3", "ff5", "carhart4"] | None = None

    @model_validator(mode="after")
    def window_must_run_forwards(self) -> Self:
        if self.end <= self.start:
            raise ValueError(f"end ({self.end}) must fall after start ({self.start})")
        return self


class PlanStep(BaseModel):
    """One tool invocation, bound to the registry at construction time."""

    id: str = Field(min_length=1)
    tool: str
    params: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    rationale: str = ""

    @model_validator(mode="after")
    def tool_and_params_must_satisfy_the_registry(self) -> Self:
        try:
            tool = get_registry().get(self.tool)
        except KeyError as exc:
            raise ValueError(f"unknown tool {self.tool!r}") from exc

        # Pydantic ignores unknown keys by default, and that is the dangerous
        # direction here: an invented `confidence` would vanish silently and
        # the user would believe the level had been honoured.
        declared = {
            name
            for field, info in tool.params_model.model_fields.items()
            for name in (field, info.alias)
            if name is not None
        }
        unknown = sorted(set(self.params) - declared)
        if unknown:
            raise ValueError(f"{self.tool}: no such parameter(s): {', '.join(unknown)}")

        try:
            tool.params_model.model_validate(self.params)
        except PydanticValidationError as exc:
            raise ValueError(f"{self.tool}: {exc}") from exc

        return self


class AnalysisPlan(BaseModel):
    """A whole analysis, before any of it has run."""

    question: str
    dataset: DatasetSpec
    steps: list[PlanStep] = Field(min_length=1)
    hypotheses: list[str] = Field(default_factory=list)
    chart_intents: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def steps_must_form_a_dag(self) -> Self:
        known: set[str] = set()
        for step in self.steps:
            if step.id in known:
                raise ValueError(f"duplicate step id {step.id!r}")
            known.add(step.id)

        for step in self.steps:
            for dependency in step.depends_on:
                if dependency not in known:
                    raise ValueError(
                        f"step {step.id!r} depends on unknown step {dependency!r}"
                    )

        self._topological_order()  # raises on a cycle
        return self

    def ordered_steps(self) -> list[PlanStep]:
        """The steps in an order that satisfies every dependency.

        Ties break on declared order, so two runs of the same plan execute
        identically — reproducibility has to reach the schedule too, not only
        the arithmetic.
        """
        return self._topological_order()

    def _topological_order(self) -> list[PlanStep]:
        by_id = {step.id: step for step in self.steps}
        pending = {step.id: set(step.depends_on) for step in self.steps}
        ordered: list[PlanStep] = []

        while pending:
            ready = [
                step.id for step in self.steps if step.id in pending and not pending[step.id]
            ]
            if not ready:
                raise ValueError(
                    f"steps form a dependency cycle: {', '.join(sorted(pending))}"
                )
            for step_id in ready:
                ordered.append(by_id[step_id])
                del pending[step_id]
            for dependencies in pending.values():
                dependencies.difference_update(ready)

        return ordered


class ValidationVerdict(BaseModel):
    """The Validator's answer on one run."""

    approved: bool
    reasons: list[str] = Field(default_factory=list)
    #: Ids of the plan steps the Validator wants reworked.
    revise_steps: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def a_rejection_must_be_actionable(self) -> Self:
        # A refusal nobody can act on is worse than no refusal: it stops the
        # run and teaches the next attempt nothing.
        if not self.approved and not self.reasons:
            raise ValueError("a rejection must give at least one reason")
        return self
