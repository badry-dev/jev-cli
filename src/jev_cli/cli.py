"""jev — TypeSafe System One CLI.

stdout carries only the API's JSON (unless --annotate is passed), so it stays
pipeable. Human-oriented notes (model, cost estimate, retries, size warnings)
go to stderr; silence them with --quiet.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from jev_cli import __version__
from jev_cli.client import DEFAULT_BASE_URL, DEFAULT_MODEL, JevClient, JevError, RetryPolicy
from jev_cli.validation import (
    PRICE_PER_MTOK_INPUT_USD,
    ValidationError,
    estimate_cost_usd,
    format_usd,
    noul_confidence,
    parse_release_date,
    size_warnings,
    validate_request,
)


def _load_json_arg(value: str) -> Any:
    """Load JSON from a file path or inline JSON string."""
    path = Path(value)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


def _print_json(data: Any, *, compact: bool = False) -> None:
    if compact:
        print(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))


def _note(args: argparse.Namespace, msg: str) -> None:
    if not getattr(args, "quiet", False):
        print(f"jev: {msg}", file=sys.stderr)


def _warn(msg: str) -> None:
    # Warnings print even with --quiet.
    print(f"jev: warning: {msg}", file=sys.stderr)


def _env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    if val is None or not val.strip():
        return default
    try:
        return int(val)
    except ValueError:
        raise JevError(f"{name} must be an integer")


def _env_float(name: str, default: float) -> float:
    val = os.environ.get(name)
    if val is None or not val.strip():
        return default
    try:
        return float(val)
    except ValueError:
        raise JevError(f"{name} must be a number")


def _retry_policy(args: argparse.Namespace) -> RetryPolicy:
    if args.no_retry:
        return RetryPolicy.disabled()
    max_retries = args.max_retries if args.max_retries is not None else _env_int("JEV_MAX_RETRIES", 4)
    budget = args.retry_budget if args.retry_budget is not None else _env_float("JEV_RETRY_BUDGET", 30.0)
    try:
        return RetryPolicy(max_retries=max_retries, total_budget=budget)
    except ValueError as e:
        raise JevError(f"Invalid retry settings: {e}")


def _client(args: argparse.Namespace) -> JevClient:
    def on_retry(attempt: int, delay: float, reason: str) -> None:
        _note(args, f"{reason} on attempt {attempt}; retrying in {delay:.2f}s")

    return JevClient(
        api_key=args.api_key,
        base_url=args.base_url,
        timeout=args.timeout,
        retry=_retry_policy(args),
        on_retry=on_retry,
    )


def cmd_models(args: argparse.Namespace) -> int:
    client = _client(args)
    result = client.models()
    if args.table:
        for m in (result or {}).get("models", []) or []:
            d = parse_release_date(m.get("release_date"))
            print(f"{m.get('name', '?'):<16} {d.isoformat() if d else '?':<10}  {m.get('description', '')}")
        return 0
    _print_json(result, compact=args.compact)
    return 0


def _summarize(args: argparse.Namespace, result: Any, attempts: int) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if not isinstance(result, dict):
        return meta
    usage = result.get("usage") or {}
    cost = estimate_cost_usd(usage.get("input_tokens"))
    meta["model"] = result.get("model")
    meta["input_tokens"] = usage.get("input_tokens")
    meta["output_tokens"] = usage.get("output_tokens")
    meta["est_cost_usd"] = cost
    meta["attempts"] = attempts
    nc: dict[str, float] = {}
    for qid, ans in (result.get("answers") or {}).items():
        if isinstance(ans, dict) and ans.get("type") == "noul":
            c = noul_confidence(ans.get("noul"))
            if c is not None:
                nc[qid] = c
    if nc:
        meta["noul_confidence"] = nc

    parts = [
        f"model={meta['model'] or '?'}",
        f"input_tokens={meta['input_tokens']}",
        f"output_tokens={meta['output_tokens']}",
        f"est_cost={format_usd(cost)} (input @ ${PRICE_PER_MTOK_INPUT_USD}/1M tok, output free)",
    ]
    if attempts > 1:
        parts.append(f"attempts={attempts}")
    if nc:
        parts.append("noul |2p-1|: " + ", ".join(f"{k}={v:.2f}" for k, v in nc.items()))
    _note(args, " ".join(parts))
    return meta


def cmd_ask(args: argparse.Namespace) -> int:
    if args.request:
        request = _load_json_arg(args.request)
        if not isinstance(request, dict):
            raise JevError("--request must be a JSON object")
        state = request.get("state")
        questions = request.get("questions")
        # An explicit --model overrides the file; otherwise the file's model, then the default.
        model = args.model or request.get("model") or DEFAULT_MODEL
        if questions is None:
            raise JevError("--request JSON must include state and questions")
    else:
        if args.state is None or args.questions is None:
            raise JevError("Provide --request, or both --state and --questions")
        state = _load_json_arg(args.state) if args.state_json else args.state
        questions = _load_json_arg(args.questions)
        model = args.model or DEFAULT_MODEL

    if args.skip_validation:
        if not isinstance(questions, dict):
            raise JevError("questions must be a JSON object map")
    else:
        try:
            questions = validate_request(state, questions)
        except ValidationError as e:
            raise JevError(
                "Request failed pre-send validation (nothing was sent)",
                body={"problems": e.problems},
            )

    for w in size_warnings(state, questions):
        _warn(w)

    client = _client(args)
    result = client.evaluate(state=state, questions=questions, model=model)
    meta = _summarize(args, result, client.last_attempts)
    if args.annotate and isinstance(result, dict):
        result = dict(result)
        result["_jev"] = meta
    _print_json(result, compact=args.compact)
    return 0


def cmd_version(_: argparse.Namespace) -> int:
    print(__version__)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jev",
        description="Thin CLI for TypeSafe System One (Jev). Secrets stay in env/local files.",
    )
    p.add_argument("--version", action="store_true", help="Print version and exit")
    p.add_argument(
        "--api-key",
        default=None,
        help="API key (prefer TYPESAFE_API_KEY env instead)",
    )
    p.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"API base URL (default: {DEFAULT_BASE_URL})",
    )
    p.add_argument(
        "--compact",
        action="store_true",
        help="Print compact JSON",
    )
    p.add_argument(
        "--quiet",
        "-Q",
        action="store_true",
        default=os.environ.get("JEV_QUIET", "").strip() not in ("", "0", "false"),
        help="Suppress the stderr summary/retry notes (warnings and errors still print). Env: JEV_QUIET=1",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Per-request timeout in seconds (default: 60)",
    )
    p.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help="Retries after the first attempt for 408/429/5xx/network errors (default: 4, env JEV_MAX_RETRIES)",
    )
    p.add_argument(
        "--retry-budget",
        type=float,
        default=None,
        help="Total seconds to keep retrying (default: 30, env JEV_RETRY_BUDGET)",
    )
    p.add_argument("--no-retry", action="store_true", help="Disable retries")

    sub = p.add_subparsers(dest="command")

    models = sub.add_parser("models", help="List available models")
    models.add_argument("--table", action="store_true", help="Print name / release date / description as text")
    models.set_defaults(func=cmd_models)

    ask = sub.add_parser("ask", help="Evaluate state against typed questions")
    ask.add_argument(
        "--request",
        "-r",
        help="Full request JSON file or inline JSON (state + questions [+ model])",
    )
    ask.add_argument("--state", "-s", help="State string (or JSON with --state-json)")
    ask.add_argument(
        "--state-json",
        action="store_true",
        help="Parse --state as JSON (file path or inline)",
    )
    ask.add_argument(
        "--questions",
        "-q",
        help="Questions JSON file or inline JSON object",
    )
    ask.add_argument(
        "--model",
        "-m",
        default=None,
        help=f"Model id, e.g. jev-1.13.0 to pin a version (default: request file's model, else {DEFAULT_MODEL})",
    )
    ask.add_argument(
        "--annotate",
        action="store_true",
        help="Add a '_jev' object to the JSON output (cost estimate, attempts, noul |2p-1|)",
    )
    ask.add_argument(
        "--skip-validation",
        action="store_true",
        help="Send without local pre-send validation",
    )
    ask.set_defaults(func=cmd_ask)

    ver = sub.add_parser("version", help="Print package version")
    ver.set_defaults(func=cmd_version)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version and not args.command:
        return cmd_version(args)

    if not args.command:
        parser.print_help()
        return 2

    try:
        return args.func(args)
    except JevError as e:
        payload: dict[str, Any] = {"error": str(e)}
        if e.status is not None:
            payload["status"] = e.status
        if e.body is not None:
            payload["body"] = e.body
        print(json.dumps(payload, indent=2), file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON: {e}"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
