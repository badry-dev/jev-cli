"""Minimal System One HTTP client (stdlib only)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
EVAL_PATH = "/v1/systemone"
MODELS_PATH = "/v1/models"


class JevError(Exception):
    """API or client error."""

    def __init__(self, message: str, *, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


def resolve_api_key(explicit: str | None = None) -> str:
    """Resolve API key from flag, env, or local config file. Never logs the value."""
    if explicit:
        return explicit.strip()

    for name in ("TYPESAFE_API_KEY", "JEV_API_KEY"):
        val = os.environ.get(name)
        if val and val.strip():
            return val.strip()

    candidates = [
        Path.home() / ".config" / "jev" / "api_key",
        Path.home() / ".config" / "jev" / "TYPESAFE_API_KEY",
    ]
    for path in candidates:
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    return text
        except OSError:
            continue

    raise JevError(
        "No API key found. Set TYPESAFE_API_KEY (or JEV_API_KEY), "
        "pass --api-key, or write the key to ~/.config/jev/api_key."
    )


class JevClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
    ):
        self.api_key = resolve_api_key(api_key)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            err_body: Any
            try:
                err_body = json.loads(e.read().decode("utf-8"))
            except Exception:
                err_body = None
            raise JevError(
                f"HTTP {e.code} from {path}",
                status=e.code,
                body=err_body,
            ) from e
        except urllib.error.URLError as e:
            raise JevError(f"Network error calling {path}: {e}") from e

    def models(self) -> dict[str, Any]:
        return self._request("GET", MODELS_PATH)

    def evaluate(
        self,
        *,
        state: Any,
        questions: dict[str, Any],
        model: str = DEFAULT_MODEL,
    ) -> dict[str, Any]:
        if not questions:
            raise JevError("questions must be a non-empty object")
        payload = {
            "model": model,
            "state": state,
            "questions": questions,
        }
        return self._request("POST", EVAL_PATH, payload)
