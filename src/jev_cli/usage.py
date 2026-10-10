"""Local usage log (JSON Lines) and summaries for `jev usage` (stdlib only).

Every `jev ask` appends one line to the usage log. The log is meant for
answering "how often do my agents call Jev, for what, at what cost, and how
confident were the answers?" without keeping the request contents.

Privacy: a line holds metadata only (caller, purpose, pattern, model, token
counts, cost estimate, latency, question ids/types/option counts, the answer
summary, and a SHA-256 of the request body). It never holds the API key, any
header, the state, or the question/criteria text.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from jev_cli.validation import estimate_cost_usd, noul_confidence

DEFAULT_LOG_PATH = Path("~/.local/share/jev/usage.jsonl")
KNOWN_PATTERNS = ("intent_routing", "confidence_gate", "urgency", "guardrail", "harness", "other")
LOW_CONFIDENCE = 0.5
META_KEYS = ("caller", "purpose", "pattern")


def _truthy(val: str | None) -> bool:
    return (val or "").strip().lower() not in ("", "0", "false", "no", "off")


def logging_disabled(no_log_flag: bool = False) -> bool:
    return bool(no_log_flag) or _truthy(os.environ.get("JEV_NO_LOG"))


def log_path(explicit: str | None = None) -> Path:
    raw = explicit or os.environ.get("JEV_USAGE_LOG") or str(DEFAULT_LOG_PATH)
    return Path(os.path.expanduser(raw))


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def request_sha256(payload: Any) -> str:
    """SHA-256 of the canonical JSON request body (sorted keys, compact)."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def normalize_pattern(value: Any) -> str | None:
    """Accept 'a', 'a+b', 'a,b' or a list; returns a '+'-joined string or None."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        parts = [str(v) for v in value]
    else:
        parts = str(value).replace(",", "+").split("+")
    parts = [p.strip() for p in parts if p and p.strip()]
    return "+".join(parts) or None


def unknown_patterns(pattern: str | None) -> list[str]:
    if not pattern:
        return []
    return [p for p in pattern.split("+") if p not in KNOWN_PATTERNS]


def question_summaries(questions: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(questions, dict):
        return out
    for qid, q in questions.items():
        q = q if isinstance(q, dict) else {}
        crit = q.get("criteria")
        qtype = q.get("type")
        n = len(crit) if isinstance(crit, (dict, list)) and qtype in ("choice", "score") else None
        out.append({"id": str(qid), "type": qtype, "n_options": n})
    return out


def answer_summaries(result: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not isinstance(result, dict):
        return out
    for qid, ans in (result.get("answers") or {}).items():
        if not isinstance(ans, dict):
            continue
        t = ans.get("type")
        if t == "choice":
            out[qid] = {"type": t, "choice": ans.get("choice"), "confidence": ans.get("confidence")}
        elif t == "score":
            out[qid] = {"type": t, "score": ans.get("score"), "confidence": ans.get("confidence")}
        elif t == "noul":
            out[qid] = {"type": t, "p": ans.get("noul")}
        else:
            out[qid] = {"type": t}
    return out


def build_entry(
    *,
    caller: str | None,
    purpose: str | None,
    pattern: str | None,
    model_requested: str | None,
    payload: Any,
    questions: Any,
    result: Any = None,
    status: Any = "ok",
    latency_ms: int | None = None,
    retries: int | None = 0,
    error: str | None = None,
    ts: str | None = None,
) -> dict[str, Any]:
    usage = (result or {}).get("usage") if isinstance(result, dict) else None
    usage = usage or {}
    inp = usage.get("input_tokens")
    entry: dict[str, Any] = {
        "ts": ts or now_iso(),
        "caller": caller,
        "purpose": purpose,
        "pattern": pattern,
        "model_requested": model_requested,
        "model_answered": result.get("model") if isinstance(result, dict) else None,
        "status": status,
        "input_tokens": inp,
        "output_tokens": usage.get("output_tokens"),
        "est_cost_usd": estimate_cost_usd(inp),
        "latency_ms": latency_ms,
        "retries": retries,
        "questions": question_summaries(questions),
        "answers": answer_summaries(result),
        "request_sha256": request_sha256(payload) if payload is not None else None,
    }
    if error:
        entry["error"] = error[:300]
    return entry


def append_entry(entry: dict[str, Any], path: Path | None = None) -> bool:
    """Append one JSON line. Never raises; warns on stderr and returns False on failure."""
    target = path or log_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return True
    except Exception as e:  # noqa: BLE001 - logging must never fail the call
        print(f"jev: warning: could not write usage log {target}: {e}", file=sys.stderr)
        return False


# ------------------------------------------------------------------ reading


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()  # treat naive as local
    return dt


def read_entries(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Return (entries, bad_line_count). Missing file -> ([], 0)."""
    entries: list[dict[str, Any]] = []
    bad = 0
    try:
        fh = open(path, encoding="utf-8")
    except FileNotFoundError:
        return entries, 0
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if isinstance(obj, dict):
                entries.append(obj)
            else:
                bad += 1
    return entries, bad


def since_from(since: str | None = None, days: int | None = None, *, now: datetime | None = None) -> datetime:
    now = now or datetime.now().astimezone()
    if since:
        d = date.fromisoformat(since)
        return datetime(d.year, d.month, d.day, tzinfo=now.tzinfo)
    n = 7 if days is None else days
    return now - timedelta(days=n)


def _entry_confidences(entry: dict[str, Any]) -> list[tuple[str, float]]:
    """(question id, confidence) per answer. Noul answers use |2p-1|."""
    out: list[tuple[str, float]] = []
    for qid, a in (entry.get("answers") or {}).items():
        if not isinstance(a, dict):
            continue
        if a.get("type") == "noul":
            c = noul_confidence(a.get("p"))
        else:
            c = a.get("confidence")
        try:
            if c is not None:
                out.append((qid, float(c)))
        except (TypeError, ValueError):
            continue
    return out


def _bucket() -> dict[str, Any]:
    return {"calls": 0, "ok": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0, "est_cost_usd": 0.0}


def _add(b: dict[str, Any], e: dict[str, Any]) -> None:
    b["calls"] += 1
    if e.get("status") == "ok":
        b["ok"] += 1
    else:
        b["errors"] += 1
    for k in ("input_tokens", "output_tokens"):
        try:
            b[k] += int(e.get(k) or 0)
        except (TypeError, ValueError):
            pass
    try:
        b["est_cost_usd"] += float(e.get("est_cost_usd") or 0.0)
    except (TypeError, ValueError):
        pass


def summarize(
    entries: Iterable[dict[str, Any]],
    *,
    start: datetime,
    end: datetime | None = None,
    low_threshold: float = LOW_CONFIDENCE,
) -> dict[str, Any]:
    total = _bucket()
    by_caller: dict[str, dict[str, Any]] = defaultdict(_bucket)
    by_pattern: dict[str, dict[str, Any]] = defaultdict(_bucket)
    by_day: dict[str, dict[str, Any]] = defaultdict(_bucket)
    statuses: Counter[str] = Counter()
    models: Counter[str] = Counter()
    confs: list[float] = []
    low: list[dict[str, Any]] = []
    total_retries = 0
    latencies: list[int] = []
    backfilled = 0
    no_purpose = 0
    first = last = None

    for e in entries:
        ts = parse_ts(e.get("ts"))
        if ts is None or ts < start or (end is not None and ts >= end):
            continue
        first = ts if first is None or ts < first else first
        last = ts if last is None or ts > last else last
        _add(total, e)
        _add(by_caller[e.get("caller") or "(unknown)"], e)
        for p in (e.get("pattern") or "(none)").split("+"):
            _add(by_pattern[p], e)
        _add(by_day[ts.date().isoformat()], e)
        statuses[str(e.get("status"))] += 1
        if e.get("model_answered"):
            models[str(e["model_answered"])] += 1
        if e.get("backfilled"):
            backfilled += 1
        if not e.get("purpose"):
            no_purpose += 1
        try:
            total_retries += int(e.get("retries") or 0)
        except (TypeError, ValueError):
            pass
        if isinstance(e.get("latency_ms"), (int, float)):
            latencies.append(int(e["latency_ms"]))
        for qid, c in _entry_confidences(e):
            confs.append(c)
            if c < low_threshold:
                low.append(
                    {"ts": e.get("ts"), "caller": e.get("caller"), "purpose": e.get("purpose"),
                     "question": qid, "confidence": round(c, 4)}
                )

    def _round(b: dict[str, Any]) -> dict[str, Any]:
        b = dict(b)
        b["est_cost_usd"] = round(b["est_cost_usd"], 10)
        return b

    return {
        "since": start.isoformat(timespec="seconds"),
        "until": (end or datetime.now().astimezone()).isoformat(timespec="seconds"),
        "first_call": first.isoformat(timespec="seconds") if first else None,
        "last_call": last.isoformat(timespec="seconds") if last else None,
        "totals": _round(total),
        "statuses": dict(statuses),
        "models_answered": dict(models),
        "retries": total_retries,
        "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "backfilled_calls": backfilled,
        "calls_without_purpose": no_purpose,
        "by_caller": {k: _round(v) for k, v in sorted(by_caller.items(), key=lambda kv: -kv[1]["calls"])},
        "by_pattern": {k: _round(v) for k, v in sorted(by_pattern.items(), key=lambda kv: -kv[1]["calls"])},
        "by_day": {k: _round(v) for k, v in sorted(by_day.items())},
        "confidence": {
            "answers": len(confs),
            "avg": round(sum(confs) / len(confs), 4) if confs else None,
            "min": round(min(confs), 4) if confs else None,
            "low_threshold": low_threshold,
            "low_count": len(low),
            "low": low,
        },
    }


def _usd(x: float) -> str:
    if not x:
        return "$0"
    return f"${x:.6f}"


def format_summary(s: dict[str, Any], path: Path | None = None) -> str:
    t = s["totals"]
    c = s["confidence"]
    lines = [
        f"Jev usage {s['since'][:10]} -> {s['until'][:10]}" + (f"  (log: {path})" if path else ""),
        f"  calls: {t['calls']}  ok: {t['ok']}  errors: {t['errors']}"
        + (f"  (backfilled: {s['backfilled_calls']})" if s["backfilled_calls"] else ""),
        f"  tokens: {t['input_tokens']:,} in / {t['output_tokens']:,} out   est cost: {_usd(t['est_cost_usd'])}",
        f"  retries: {s['retries']}"
        + (f"   avg latency: {s['avg_latency_ms']} ms" if s["avg_latency_ms"] is not None else ""),
    ]
    if s["models_answered"]:
        lines.append("  models: " + ", ".join(f"{k} x{v}" for k, v in s["models_answered"].items()))
    if len(s["statuses"]) > 1 or (s["statuses"] and "ok" not in s["statuses"]):
        lines.append("  statuses: " + ", ".join(f"{k}={v}" for k, v in s["statuses"].items()))
    if s["calls_without_purpose"]:
        lines.append(f"  calls without --purpose: {s['calls_without_purpose']}")
    for title, key in (("by caller", "by_caller"), ("by pattern", "by_pattern"), ("by day", "by_day")):
        if not s[key]:
            continue
        lines.append(f"  {title}:")
        for name, b in s[key].items():
            lines.append(
                f"    {name:<28} {b['calls']:>4} calls  {b['input_tokens']:>8,} in  {_usd(b['est_cost_usd'])}"
            )
    if c["answers"]:
        lines.append(
            f"  confidence: {c['answers']} answers  avg {c['avg']:.2f}  min {c['min']:.2f}  "
            f"low (<{c['low_threshold']}): {c['low_count']}"
        )
        for item in c["low"]:
            lines.append(
                f"    low: {item['question']}={item['confidence']:.2f}  [{item['caller']}] {item['purpose'] or ''}"
            )
    else:
        lines.append("  confidence: no answers in range")
    return "\n".join(lines)
