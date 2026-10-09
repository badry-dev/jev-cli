"""Pre-send request validation, size estimates, and cost helpers (stdlib only).

Validation mirrors the checks in the official typesafe-sdk (0.7.x) so obvious
mistakes fail locally with a clear message instead of costing a round trip.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

# Pricing / limits from https://docs.typesafe.ai/models.md (Jev 1.13).
PRICE_PER_MTOK_INPUT_USD = 0.042  # output tokens are free
MAX_TOTAL_TOKENS = 64_000  # state + all questions
MAX_STATE_PLUS_LONGEST_QUESTION_TOKENS = 32_000
WARN_RATIO = 0.8  # warn once an estimate passes 80% of a limit
CHARS_PER_TOKEN = 4  # rough heuristic, labeled as an estimate everywhere

MAX_CHOICE_OPTIONS = 255
MIN_SCORE_LEVELS = 2
MAX_SCORE_LEVELS = 10
QUESTION_TYPES = ("noul", "choice", "score")


class ValidationError(ValueError):
    """Request failed local pre-send validation."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (dict, list)):
        return len(value) == 0
    return False


def _int_key(key: Any) -> int | None:
    try:
        return int(str(key).strip())
    except (TypeError, ValueError):
        return None


def _validate_question(qid: str, q: Any, problems: list[str]) -> Any:
    """Validate one question; returns a (possibly normalized) copy."""
    where = f"questions.{qid}"
    if not isinstance(q, dict):
        problems.append(f"{where}: must be an object with a 'type' field")
        return q
    qtype = q.get("type")
    if qtype not in QUESTION_TYPES:
        problems.append(
            f"{where}.type: must be one of {', '.join(QUESTION_TYPES)} (got {qtype!r})"
        )
        return q

    q = dict(q)
    criteria = q.get("criteria")

    if qtype == "choice":
        if criteria is None:
            problems.append(f"{where}.criteria: choice questions need criteria (option -> description)")
        elif isinstance(criteria, list):
            # Convenience: ["a", "b"] -> {"a": null, "b": null} (API takes a map).
            names = [c for c in criteria if isinstance(c, str) and c.strip()]
            if len(names) != len(criteria):
                problems.append(f"{where}.criteria: list form must contain only non-empty option names")
            elif len(set(names)) != len(names):
                problems.append(f"{where}.criteria: option names must be unique")
            elif not names:
                problems.append(f"{where}.criteria: must not be empty")
            elif len(names) > MAX_CHOICE_OPTIONS:
                problems.append(
                    f"{where}.criteria: {len(names)} options; the API allows at most {MAX_CHOICE_OPTIONS}"
                )
            else:
                q["criteria"] = {n: None for n in names}
        elif isinstance(criteria, dict):
            if not criteria:
                problems.append(f"{where}.criteria: must not be empty")
            elif len(criteria) > MAX_CHOICE_OPTIONS:
                problems.append(
                    f"{where}.criteria: {len(criteria)} options; the API allows at most {MAX_CHOICE_OPTIONS}"
                )
        else:
            problems.append(f"{where}.criteria: must be an object (option -> description) or a list of option names")

    elif qtype == "score":
        if criteria is None:
            problems.append(f"{where}.criteria: score questions need an ordered list of levels")
        elif isinstance(criteria, dict):
            keys = [_int_key(k) for k in criteria]
            if criteria and all(k is not None for k in keys):
                ordered = [criteria[k] for k in sorted(criteria, key=lambda k: _int_key(k))]
                hint = json.dumps(ordered, ensure_ascii=False)
                if len(hint) > 160:
                    hint = hint[:157] + "..."
                problems.append(
                    f"{where}.criteria: the dict-keyed-by-integers form was removed "
                    f"(typesafe-sdk 0.6.0). Pass an ordered list instead, lowest level first: {hint}"
                )
            else:
                problems.append(f"{where}.criteria: must be an ordered list of level descriptions")
        elif not isinstance(criteria, list):
            problems.append(f"{where}.criteria: must be an ordered list of level descriptions")
        elif not (MIN_SCORE_LEVELS <= len(criteria) <= MAX_SCORE_LEVELS):
            problems.append(
                f"{where}.criteria: has {len(criteria)} level(s); score needs "
                f"{MIN_SCORE_LEVELS}-{MAX_SCORE_LEVELS} levels"
            )
        elif any(c is None for c in criteria):
            problems.append(f"{where}.criteria: levels must not be null")

    elif qtype == "noul":
        if _is_blank(q.get("instructions")) and _is_blank(criteria):
            problems.append(f"{where}: noul questions need instructions or criteria")
        if criteria is not None:
            if not isinstance(criteria, dict):
                problems.append(f"{where}.criteria: must be an object with 'true' and/or 'false'")
            else:
                extra = sorted(set(criteria) - {"true", "false"})
                if extra:
                    problems.append(
                        f"{where}.criteria: unknown key(s) {extra}; only 'true' and 'false' are allowed"
                    )
    return q


def validate_request(state: Any, questions: Any) -> dict[str, Any]:
    """Validate state + questions. Returns normalized questions or raises ValidationError."""
    problems: list[str] = []

    if state is None:
        problems.append("state: must not be null")
    elif not isinstance(state, (str, dict, list)):
        problems.append(f"state: must be a string, object, or array (got {type(state).__name__})")
    elif isinstance(state, str) and not state.strip():
        problems.append("state: must not be empty")

    normalized: dict[str, Any] = {}
    if not isinstance(questions, dict):
        problems.append("questions: must be a JSON object map of id -> question")
    elif not questions:
        problems.append("questions: must contain at least one question")
    else:
        for qid, q in questions.items():
            if not str(qid).strip():
                problems.append("questions: question ids must be non-empty")
                continue
            normalized[qid] = _validate_question(qid, q, problems)

    if problems:
        raise ValidationError(problems)
    return normalized


# ---------------------------------------------------------------- size / cost


def estimate_tokens(value: Any) -> int:
    """Rough token estimate (chars / 4 of the JSON encoding). An estimate, not a tokenizer."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def size_warnings(state: Any, questions: dict[str, Any]) -> list[str]:
    """Warnings when the estimated request size approaches documented limits."""
    state_tok = estimate_tokens(state)
    q_toks = [estimate_tokens(q) for q in questions.values()] or [0]
    total = state_tok + sum(q_toks)
    pair = state_tok + max(q_toks)
    out: list[str] = []
    for label, est, limit in (
        ("state + all questions", total, MAX_TOTAL_TOKENS),
        ("state + longest question", pair, MAX_STATE_PLUS_LONGEST_QUESTION_TOKENS),
    ):
        if est > limit:
            out.append(
                f"estimated ~{est:,} tokens for {label} (chars/4 estimate) exceeds the "
                f"{limit:,}-token limit; the API will likely reject this request"
            )
        elif est >= WARN_RATIO * limit:
            out.append(
                f"estimated ~{est:,} tokens for {label} (chars/4 estimate) is close to the "
                f"{limit:,}-token limit"
            )
    return out


def estimate_cost_usd(input_tokens: Any, price_per_mtok: float = PRICE_PER_MTOK_INPUT_USD) -> float | None:
    """Cost estimate from usage.input_tokens. Output tokens are free."""
    try:
        n = int(input_tokens)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    return n * price_per_mtok / 1_000_000


def format_usd(amount: float | None) -> str:
    if amount is None:
        return "n/a"
    if amount == 0:
        return "$0"
    if amount < 0.01:
        return f"${amount:.8f}".rstrip("0").rstrip(".")
    return f"${amount:.4f}"


def noul_confidence(p: Any) -> float | None:
    """Confidence-style distance from 0.5 for a noul probability: |2p - 1|."""
    try:
        x = float(p)
    except (TypeError, ValueError):
        return None
    if not 0.0 <= x <= 1.0:
        return None
    return round(abs(2 * x - 1), 4)


def parse_release_date(value: Any) -> date | None:
    """Lenient release_date parse: YYYY-MM-DD or full ISO-8601 datetime (Z ok)."""
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None
