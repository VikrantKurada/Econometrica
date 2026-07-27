"""The numeric grounding gate.

Every number in a narrator's prose is extracted and matched against the
numbers the tools actually computed. Unmatched numbers block the message.

This is the one anti-hallucination check in the system that is mechanical
rather than persuasive, which is why the design calls it the most important
one. It is also the one most easily ruined by over-strictness: a gate that
blocks "significant at the 5% level" or "shown in figure 2" gets switched off
within a day, and a gate that is off protects nothing. So the exemptions below
are deliberate, narrow, and each has a test — including a test that the
exemption does not apply outside its context.

Precision comes from the citation, not from a global epsilon. "1.30" claims
two decimal places, so it matches any computed value that rounds to 1.30 at
two places; "1.3" claims one. That is both stricter and more permissive than a
fixed tolerance, in the right directions.
"""

import re
from collections.abc import Iterable, Sequence
from typing import Any

from pydantic import BaseModel, Field

from econometrica.econ.types import ResultSet

#: A number as prose writes one: optional sign, thousands separators, decimals,
#: exponent, trailing percent.
_NUMBER = re.compile(
    r"(?P<sign>[-+]?)"
    r"(?P<int>\d{1,3}(?:,\d{3})+|\d+)"
    r"(?P<frac>\.\d+)?"
    r"(?P<exp>[eE][-+]?\d+)?"
    r"(?P<pct>%)?"
)

#: Words that make a following number a reference rather than a claim.
_REFERENCE_WORDS = frozenset(
    {
        "figure", "fig", "figures", "table", "tables", "chart", "exhibit",
        "panel", "step", "section", "appendix", "model", "equation", "note",
    }
)

#: Words that make a following four-digit number a date rather than a result.
_DATE_WORDS = frozenset(
    {
        "in", "since", "during", "from", "to", "by", "until", "through",
        "after", "before", "of", "and", "between", "the", "over",
    }
)

#: Significance levels every reader recognises. Exempt only where the sentence
#: is talking about significance — "returns rose 5%" is a claim about data.
_CONVENTIONAL_LEVELS = frozenset({1.0, 5.0, 10.0, 0.01, 0.05, 0.10})
_SIGNIFICANCE_WORDS = ("level", "significan", "confidence", "reject", "alpha")

YEAR_RANGE = (1900, 2100)

#: Every dash a date range might be written with: the ASCII hyphen, the
#: U+2010..U+2015 block (hyphen, non-breaking hyphen, figure dash, en dash, em
#: dash, horizontal bar) and the true minus sign. Built from code points rather
#: than typed as literals because all seven are indistinguishable on sight —
#: and which one appears is not a choice anybody makes deliberately: a model
#: writing a heading reaches for the en dash, a person for the hyphen.
_DASH = "[" + "-" + "".join(chr(point) for point in range(0x2010, 0x2016)) + chr(0x2212) + "]"


class GroundingIssue(BaseModel):
    """One number in the prose that no computed value supports."""

    value: float
    #: As written, so the revision prompt can quote it back.
    text: str
    sentence: str


class GroundingReport(BaseModel):
    grounded: bool
    issues: list[GroundingIssue] = Field(default_factory=list)
    #: How many numbers were examined, exemptions excluded.
    checked: int = 0

    def summary(self) -> str:
        """What to tell the narrator so the next draft is different."""
        if self.grounded:
            return "every number is supported by a computed result"
        return "; ".join(
            f"{issue.text!r} in {issue.sentence.strip()!r} matches no computed value"
            for issue in self.issues
        )


def allowed_values(results: Iterable[ResultSet]) -> set[float]:
    """Every number a narrator is permitted to cite.

    ``ResultSet.all_numeric_values()`` covers outputs only. The parameters are
    included too, because a narrator may legitimately name the model it
    fitted — and without them "a GARCH(1,1) fit" reads as two fabrications.
    """
    values: set[float] = set()
    for result in results:
        values |= result.all_numeric_values()
        values |= _numbers_in(result.params)
    return values


def check_grounding(
    prose: str, allowed: set[float], *, step_ids: Iterable[str] = ()
) -> GroundingReport:
    """Match every number in ``prose`` against ``allowed``.

    ``step_ids`` are the plan's own step identifiers. Supplying them exempts
    citations of steps that exist — see `_is_step_citation`. They default to
    empty so the exemption is something a caller opts into with real ids,
    never a hole that opens by itself.
    """
    known = frozenset(step.lower() for step in step_ids)
    issues: list[GroundingIssue] = []
    checked = 0

    for sentence in _sentences(prose):
        for match in _NUMBER.finditer(sentence):
            if _exempt(match, sentence, known):
                continue
            checked += 1
            if _supported(match, allowed):
                continue
            issues.append(
                GroundingIssue(
                    value=_value(match),
                    text=match.group(0),
                    sentence=sentence,
                )
            )

    return GroundingReport(grounded=not issues, issues=issues, checked=checked)


# --- matching ---------------------------------------------------------------


def _value(match: re.Match[str]) -> float:
    text = match.group("sign") + match.group("int").replace(",", "")
    text += match.group("frac") or ""
    text += match.group("exp") or ""
    return float(text)


def _supported(match: re.Match[str], allowed: set[float]) -> bool:
    value = _value(match)
    decimals = len(match.group("frac") or "") - 1 if match.group("frac") else 0

    # A percentage is ambiguous by construction: "83%" may cite 83 or 0.83.
    # Either reading is honest, so either grounds it — and dividing by 100
    # buys two decimal places of claimed precision.
    candidates = [(value, decimals)]
    if match.group("pct"):
        candidates.append((value / 100.0, decimals + 2))

    for candidate, places in candidates:
        for computed in allowed:
            if _rounds_to(computed, candidate, places):
                return True
    return False


def _rounds_to(computed: float, cited: float, decimals: int) -> bool:
    """Whether ``computed``, rounded to the precision the citation claims, is it."""
    if decimals > 12:  # scientific notation; fall back to relative closeness
        scale = max(abs(cited), 1e-12)
        return abs(computed - cited) <= 1e-6 * scale
    return abs(round(computed, decimals) - cited) <= 10.0 ** -(decimals + 3)


# --- exemptions -------------------------------------------------------------


def _exempt(match: re.Match[str], sentence: str, step_ids: frozenset[str]) -> bool:
    before = sentence[: match.start()]
    previous = _last_word(before)

    if previous in _REFERENCE_WORDS:
        return True  # "figure 2", "step 3"

    if _is_step_citation(match, before, step_ids):
        return True

    if _is_list_marker(match, sentence):
        return True

    if _is_year(match, previous, sentence):
        return True

    if _is_model_order(match, sentence):
        return True

    return _is_conventional_level(match, sentence)


def _is_step_citation(
    match: re.Match[str], before: str, step_ids: frozenset[str]
) -> bool:
    """A reference to a plan step — `(s3)`, `(s1, s3)`, "as s3 found".

    `narrator._render` labels every result block `## s3 - tool`, so this is the
    citation form the project's own prompts produce. The gate used to read the
    `3` as a claim about data and withhold the whole narration over it: the
    `step 3` spelling was exempt but `s3` was not.

    **Keyed to the plan's actual ids, not to the letter.** `s7` where no s7 was
    planned cites nothing, so it stays checked — which is what keeps this from
    being a hole shaped like any letter followed by any digits. It also means
    no assumption about how a Planner names its steps: whatever `PlanStep.id`
    holds is what is recognised.
    """
    if not step_ids:
        return False
    # Whole integers only. `s1.47` is not a step id, and admitting it would let
    # a fabricated figure through behind a prefix.
    if match.group("frac") or match.group("pct") or match.group("sign"):
        return False

    prefix = re.search(r"[A-Za-z]+$", before)
    if prefix is None:
        return False
    return f"{prefix.group(0)}{match.group('int')}".lower() in step_ids


def _last_word(before: str) -> str:
    words = re.findall(r"[A-Za-z]+", before)
    return words[-1].lower() if words else ""


def _is_list_marker(match: re.Match[str], sentence: str) -> bool:
    """A markdown ordinal at the start of a line enumerates, it does not claim.

    Restricted to bare integers so that a line opening with a real figure —
    "1.2977 was the estimate" — is still checked. Without that, the exemption
    would cover exactly the numbers it must not.
    """
    if match.group("frac") or match.group("pct") or match.group("sign"):
        return False

    line_start = sentence.rfind("\n", 0, match.start()) + 1
    if sentence[line_start : match.start()].strip():
        return False

    # `$` as well as whitespace: the sentence splitter can leave the marker
    # standing alone as "1." once the text after it starts a new sentence.
    return bool(re.match(r"[.)](\s|$)", sentence[match.end() : match.end() + 2]))


def _is_year(match: re.Match[str], previous: str, sentence: str) -> bool:
    """A four-digit year, but only where the sentence is talking about time.

    "in 2008" is a date; "the statistic is 2008" is a claim, and exempting it
    would leave a hole exactly four digits wide.
    """
    if match.group("frac") or match.group("pct"):
        return False
    digits = match.group("int")
    if len(digits) != 4 or not digits.isdigit():
        return False
    if not YEAR_RANGE[0] <= int(digits) <= YEAR_RANGE[1]:
        return False

    if _is_year_range(match, sentence):
        return True
    # A sign makes it arithmetic rather than a date: "-2024" is a number.
    return not match.group("sign") and previous in _DATE_WORDS


def _is_year_range(match: re.Match[str], sentence: str) -> bool:
    """`2020-2024` — a window, not two findings.

    Found by running a real narration: a model titled its answer "... Log
    Returns (2020-2024)" and the gate withheld the whole thing over the years
    in its own heading, because `_is_year` looks at the preceding *word* and in
    a range that word is whatever the title happened to say. The window is in
    the plan and is rendered into the prompt, so restating it is what the model
    was asked to do.

    A year is required on *both* sides, which is what keeps a lone four-digit
    finding checked. The hyphen spelling needs its own handling: `-2024` parses
    with a sign, so it arrives looking like a negative number.
    """
    after = re.match(rf"\s*{_DASH}?\s*(\d{{4}})\b", sentence[match.end() :])
    if after is not None and _is_year_number(after.group(1)):
        return True

    # The dash may already have been consumed as this number's sign, so it is
    # optional on this side too.
    before = re.search(rf"\b(\d{{4}})\s*{_DASH}?\s*$", sentence[: match.start()])
    return before is not None and _is_year_number(before.group(1))


def _is_year_number(digits: str) -> bool:
    return YEAR_RANGE[0] <= int(digits) <= YEAR_RANGE[1]


def _is_model_order(match: re.Match[str], sentence: str) -> bool:
    """Orders inside a model name — GARCH(1,1), VAR(2), ARMA(1,1).

    These name the specification rather than reporting a finding, and the
    specification is already in the plan the user can read.
    """
    opening = sentence.rfind("(", 0, match.start())
    if opening == -1:
        return False
    closing = sentence.find(")", match.end() - 1)
    if closing == -1:
        return False
    inside = sentence[opening + 1 : closing]
    if not re.fullmatch(r"[\d,\s]+", inside):
        return False
    # Directly attached to a name, as in "GARCH(1,1)" but not "(1,1)".
    return bool(re.search(r"[A-Za-z]$", sentence[:opening]))


def _is_conventional_level(match: re.Match[str], sentence: str) -> bool:
    value = _value(match)
    if value not in _CONVENTIONAL_LEVELS:
        return False
    lowered = sentence.lower()
    return any(word in lowered for word in _SIGNIFICANCE_WORDS)


# --- text -------------------------------------------------------------------


def _sentences(prose: str) -> list[str]:
    """Split for reporting. A decimal point must not end a sentence, so the
    split needs a following capital or a line break to fire."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])|\n", prose)
    return [part for part in parts if part.strip()]


def _numbers_in(value: Any) -> set[float]:
    """Every number reachable inside a parameter structure."""
    if isinstance(value, bool):
        return set()  # bool is an int in Python, and True is not a citation
    if isinstance(value, int | float):
        return {float(value)}
    if isinstance(value, dict):
        return {n for item in value.values() for n in _numbers_in(item)}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return {n for item in value for n in _numbers_in(item)}
    return set()
