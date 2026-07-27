"""The Quant Coder: the escape hatch, and the only agent that produces numbers.

Every other result in this system comes from a registry tool — typed,
versioned, unit-tested, with preconditions the Econometrician enforces before
it will run. §2 of the design chose to keep one door out of that, because a
registry is bounded and the questions are not: when nothing fits, a model
writes code and `sandbox/` runs it where it can do least.

**The marking is the feature.** A result from here has no tested function
behind it, so it must never look like one that has. It says so in the two
places anything reads — `ResultSet.tool` is `sandbox:<method>` and the
manifest's version is `unvalidated` — and `is_sandbox_result` is what the
canvas, the exports and the print stylesheet key off. Three conditions gate it,
and `check_permitted` refuses rather than degrades: the capability is on, the
tier has a Validator, and the code survives the sandbox.

The retry here is a *second* loop outside `Agent.ask`'s. That one exists for a
reply that could not be parsed; this one for code that parsed, ran and failed —
which is a different conversation, and the one where showing the model its own
traceback is worth the most.
"""

from __future__ import annotations

import hashlib
import re
from importlib import metadata

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from econometrica.agents.base import Agent, AgentAttemptsExhaustedError
from econometrica.econ.fingerprint import fingerprint_frame
from econometrica.econ.types import Diagnostic, Estimate, Manifest, ResultSet, Series, Table
from econometrica.llm.base import LLMProvider
from econometrica.llm.types import Completion, Message
from econometrica.sandbox.policy import ALLOWED_IMPORTS, SandboxLimits
from econometrica.sandbox.runner import SandboxOutcome, run_sandboxed

#: Every sandbox result's tool name begins with this. A colon cannot appear in
#: a registry tool name, so nothing in `econ/` can collide with it by accident.
SANDBOX_TOOL_PREFIX = "sandbox:"

#: Stands where a registry tool's semantic version stands. Not a number,
#: deliberately: there is nothing to compare it against, and "1.0.0" would
#: imply a tested contract that does not exist.
UNVALIDATED_VERSION = "unvalidated"

#: Tiers with no Validator. §2 makes sign-off mandatory for this path.
_TIERS_WITHOUT_REVIEW = frozenset({"single"})

_LIBRARIES = ("numpy", "pandas", "scipy", "statsmodels")

_SYSTEM = """\
You are the Quant Coder in an econometrics workbench. You are asked for a
calculation only because no tool in the registry performs it — so write the
smallest correct thing, not a framework.

Your code runs in a locked-down subprocess: no network, no filesystem, no other
processes, and only these imports:

{allowed}

`frame` is already bound to a pandas DataFrame, and `np`, `pd` and `sm`
(statsmodels.api) are already imported. Assign a dict named `result`. Its keys
may only be:

  scalars      {{"name": number}}
  estimates    [{{"name": ..., "value": ..., "std_error": ..., "t_stat": ...,
                 "p_value": ..., "ci_low": ..., "ci_high": ...}}]
  diagnostics  [{{"name": ..., "statistic": ..., "p_value": ...,
                 "passed": true/false/null, "interpretation": ...}}]
  series       {{"name": {{"name": ..., "x": [...], "y": [...]}}}}
  tables       {{"name": {{"columns": [...], "rows": [[...]]}}}}

Anything else is rejected. `passed` is three-valued: null means the check was
not judged, never that it failed.

Reply with a single JSON object and nothing else:

{{"method": "a short name for what you computed",
 "code": "the python, with \\n for newlines",
 "rationale": "why the registry could not do this",
 "assumptions": ["what the reader has to accept for this to be valid"]}}\
"""


class SandboxNotPermittedError(RuntimeError):
    """The escape hatch was reached without the conditions that gate it."""


class CodeDraft(BaseModel):
    """What the model wrote."""

    method: str = Field(min_length=1)
    code: str = Field(min_length=1)
    rationale: str = ""
    #: What a reader has to accept for the number to mean anything. Surfaced
    #: beside the result, because an unvalidated method's assumptions are the
    #: only thing standing in for a precondition gate.
    assumptions: list[str] = Field(default_factory=list)


class SandboxPayload(BaseModel):
    """The `result` dict, as the trusted side is willing to read it.

    `extra="forbid"` is the point: a model that returns `{"conclusion": ...}`
    has reported something the canvas cannot draw and the manifest cannot
    describe, and silently dropping it would leave the model believing it had
    answered.
    """

    model_config = ConfigDict(extra="forbid")

    estimates: list[Estimate] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    scalars: dict[str, float] = Field(default_factory=dict)
    tables: dict[str, Table] = Field(default_factory=dict)
    series: dict[str, Series] = Field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (
            self.estimates or self.diagnostics or self.scalars or self.tables or self.series
        )


class CodeRun(BaseModel):
    """One trip through the escape hatch, published or not.

    A model rather than a dataclass for the same reason `Narration` is one: it
    travels the run's SSE stream, and a client explaining why no result
    appeared needs the denials and the error, not a status word.
    """

    published: bool
    method: str = ""
    draft: CodeDraft | None = None
    result: ResultSet | None = None
    status: str = "failed"
    error: str = ""
    #: Restrictions the code ran into. Non-empty means it tried to get out.
    denials: list[str] = Field(default_factory=list)
    stdout: str = ""
    attempts: int = 0
    #: Every attempt, published or not — each was billed.
    completions: list[Completion] = Field(default_factory=list)
    #: What each attempt was sent, paired with `completions` by position, so
    #: the trace can show the code the model was asked to fix. Same contract
    #: `AgentResult.prompts` keeps.
    prompts: list[str] = Field(default_factory=list)


def check_permitted(*, enabled: bool, tier: str) -> None:
    """The three gates, two of which are checkable before anything runs.

    Refuses rather than degrades. Silently skipping a code step would leave a
    plan whose recorded steps do not match its results, which is worse than a
    run that stops and says why.
    """
    if not enabled:
        raise SandboxNotPermittedError(
            "the code sandbox is not enabled for this project; it is off by default"
            " and can only be turned on at project level"
        )
    if tier in _TIERS_WITHOUT_REVIEW:
        raise SandboxNotPermittedError(
            f"the {tier!r} validation tier has no Validator, and generated code"
            " requires sign-off before its results may surface"
        )


def is_sandbox_result(result: ResultSet) -> bool:
    """Whether this number came from generated code rather than a tested tool.

    What the canvas, the exports and `Provenance` key off. Derived from the
    result itself rather than carried alongside it, for the reason the
    synthetic-data flag is derived from the source label: a marker that travels
    separately is a marker that can be lost.
    """
    return result.tool.startswith(SANDBOX_TOOL_PREFIX)


class QuantCoder(Agent[CodeDraft]):
    role = "quant_coder"

    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        *,
        max_attempts: int = 2,
        max_executions: int = 2,
    ) -> None:
        super().__init__(provider, model, max_attempts=max_attempts)
        self.max_executions = max_executions

    def output_model(self) -> type[CodeDraft]:
        return CodeDraft

    def check(self, output: CodeDraft) -> None:
        # A contract check, not a security one — nothing here is trusted to
        # detect a hostile import, and the sandbox does not need it to.
        if not re.search(r"^\s*result\s*=", output.code, re.MULTILINE):
            raise ValueError(
                "the code must assign a dict named `result`; nothing else is read back"
            )

    async def compute(
        self,
        intent: str,
        frame: pd.DataFrame,
        *,
        context: str = "",
        limits: SandboxLimits | None = None,
    ) -> CodeRun:
        """Write code for `intent`, run it, and mark whatever it produced."""
        conversation = [
            Message.system(_SYSTEM.format(allowed=", ".join(sorted(ALLOWED_IMPORTS)))),
            Message.user(_task(intent, frame, context)),
        ]

        completions: list[Completion] = []
        prompts: list[str] = []
        draft: CodeDraft | None = None
        outcome: SandboxOutcome | None = None
        problem = ""

        for attempt in range(1, self.max_executions + 1):
            try:
                asked = await self.ask(conversation)
            except AgentAttemptsExhaustedError as exc:
                completions.extend(exc.completions)
                prompts.extend(exc.prompts)
                return CodeRun(
                    published=False,
                    error=str(exc),
                    attempts=attempt,
                    completions=completions,
                    prompts=prompts,
                )

            completions.extend(asked.completions)
            prompts.extend(asked.prompts)
            draft = asked.output
            outcome = await run_sandboxed(draft.code, frame, limits=limits or SandboxLimits())
            problem = _problem(outcome)

            if problem == "":
                payload = SandboxPayload.model_validate(outcome.payload)
                return CodeRun(
                    published=True,
                    method=draft.method,
                    draft=draft,
                    result=_as_result(draft, payload, frame),
                    status=outcome.status,
                    stdout=outcome.stdout,
                    attempts=attempt,
                    completions=completions,
                    prompts=prompts,
                )

            if attempt < self.max_executions:
                conversation.append(Message.assistant(asked.completions[-1].content))
                conversation.append(Message.user(_RETRY.format(problem=problem)))

        return CodeRun(
            published=False,
            method=draft.method if draft else "",
            draft=draft,
            status=outcome.status if outcome else "failed",
            error=problem,
            denials=list(outcome.denials) if outcome else [],
            stdout=outcome.stdout if outcome else "",
            attempts=self.max_executions,
            completions=completions,
            prompts=prompts,
        )


_RETRY = (
    "That code did not produce a usable result.\n\n{problem}\n\n"
    "Reply again with the corrected JSON object only — no prose, no code fence."
)


def _problem(outcome: SandboxOutcome) -> str:
    """Why this attempt is not an answer, phrased for the next attempt.

    A denial is reported as a denial rather than as a crash: telling a model
    "your code failed" when it tried to open a socket invites it to try a
    different socket.
    """
    if outcome.status == "denied":
        return (
            "The code was refused by the sandbox: "
            + "; ".join(outcome.denials or [outcome.error])
            + ". Use only the permitted imports and touch nothing outside `frame`."
        )
    if outcome.status != "ok":
        return f"The code {outcome.status.replace('_', ' ')}: {outcome.error}"

    try:
        payload = SandboxPayload.model_validate(outcome.payload)
    except ValueError as exc:
        return f"The `result` dict was not usable: {exc}"
    if payload.is_empty():
        return (
            "The `result` dict was empty — nothing was reported."
            " Populate at least one of scalars, estimates, diagnostics, series or tables."
        )
    return ""


def _task(intent: str, frame: pd.DataFrame, context: str) -> str:
    columns = ", ".join(f"{name} ({frame[name].dtype})" for name in frame.columns)
    index = "a DatetimeIndex" if isinstance(frame.index, pd.DatetimeIndex) else "a plain index"
    parts = [
        f"Compute: {intent}",
        f"`frame` has {len(frame)} rows on {index}, with columns: {columns}",
    ]
    if context:
        parts.append(context)
    return "\n\n".join(parts)


def _as_result(draft: CodeDraft, payload: SandboxPayload, frame: pd.DataFrame) -> ResultSet:
    """The payload as a `ResultSet`, marked.

    Built here rather than in the child: the sandbox emits JSON and the trusted
    side decides whether that JSON is a result. `params` carries the code
    itself, so a reader can see exactly what produced the number without
    leaving the artifact.
    """
    tool = SANDBOX_TOOL_PREFIX + _slug(draft.method)
    return ResultSet(
        tool=tool,
        version=UNVALIDATED_VERSION,
        params={
            "method": draft.method,
            "code": draft.code,
            "rationale": draft.rationale,
            "assumptions": draft.assumptions,
        },
        estimates=payload.estimates,
        diagnostics=payload.diagnostics,
        scalars=payload.scalars,
        tables=payload.tables,
        series=payload.series,
        manifest=Manifest(
            data_fingerprint=fingerprint_frame(frame),
            tool=tool,
            tool_version=UNVALIDATED_VERSION,
            # The *code* is the parameterisation. Two methods over one frame
            # are two analyses, and a manifest that hashed only the method name
            # would call a rewritten body the same study.
            params_hash=hashlib.sha256(draft.code.encode("utf-8")).hexdigest(),
            library_versions={name: metadata.version(name) for name in _LIBRARIES},
        ),
    )


def _slug(method: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", method.lower()).strip("_")
    return cleaned or "method"
