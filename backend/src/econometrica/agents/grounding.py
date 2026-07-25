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


def check_grounding(prose: str, allowed: set[float]) -> GroundingReport:
    """Match every number in ``prose`` against ``allowed``."""
    issues: list[GroundingIssue] = []
    checked = 0

    for sentence in _sentences(prose):
        for match in _NUMBER.finditer(sentence):
            if _exempt(match, sentence):
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


def _exempt(match: re.Match[str], sentence: str) -> bool:
    before = sentence[: match.start()]
    previous = _last_word(before)

    if previous in _REFERENCE_WORDS:
        return True  # "figure 2", "step 3"

    if _is_list_marker(match, sentence):
        return True

    if _is_year(match, previous):
        return True

    if _is_model_order(match, sentence):
        return True

    return _is_conventional_level(match, sentence)


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


def _is_year(match: re.Match[str], previous: str) -> bool:
    """A four-digit year, but only where the sentence is talking about time.

    "in 2008" is a date; "the statistic is 2008" is a claim, and exempting it
    would leave a hole exactly four digits wide.
    """
    if match.group("frac") or match.group("pct") or match.group("sign"):
        return False
    digits = match.group("int")
    if len(digits) != 4 or not digits.isdigit():
        return False
    if not YEAR_RANGE[0] <= int(digits) <= YEAR_RANGE[1]:
        return False
    return previous in _DATE_WORDS


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
