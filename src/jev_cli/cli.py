"""jev — TypeSafe System One CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jev_cli import __version__
from jev_cli.client import DEFAULT_BASE_URL, DEFAULT_MODEL, JevClient, JevError


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


def cmd_models(args: argparse.Namespace) -> int:
    client = JevClient(api_key=args.api_key, base_url=args.base_url)
    result = client.models()
    _print_json(result, compact=args.compact)
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    if args.request:
        request = _load_json_arg(args.request)
        if not isinstance(request, dict):
            raise JevError("--request must be a JSON object")
        state = request.get("state")
        questions = request.get("questions")
        model = request.get("model", args.model)
        if state is None or questions is None:
            raise JevError("--request JSON must include state and questions")
    else:
        if args.state is None or args.questions is None:
            raise JevError("Provide --request, or both --state and --questions")
        state = _load_json_arg(args.state) if args.state_json else args.state
        questions = _load_json_arg(args.questions)
        model = args.model

    if not isinstance(questions, dict):
        raise JevError("questions must be a JSON object map")

    client = JevClient(api_key=args.api_key, base_url=args.base_url)
    result = client.evaluate(state=state, questions=questions, model=model)
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

    sub = p.add_subparsers(dest="command")

    models = sub.add_parser("models", help="List available models")
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
        default=DEFAULT_MODEL,
        help=f"Model id (default: {DEFAULT_MODEL})",
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
