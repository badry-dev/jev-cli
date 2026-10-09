"""Minimal System One HTTP client (stdlib only) with retries."""

from __future__ import annotations

import http.client
import json
import os
import random
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from jev_cli import __version__

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
EVAL_PATH = "/v1/systemone"
MODELS_PATH = "/v1/models"
USER_AGENT = f"jev-cli/{__version__}"

# 408, 429 and every 5xx (529 = TypeSafe "Overloaded"). 400/401/403/404/422 never retry.
RETRY_STATUSES = frozenset({408, 429, *range(500, 600)})


class JevError(Exception):
    """API or client error."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: Any = None,
        attempts: int | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.body = body
        self.attempts = attempts


@dataclass
class RetryPolicy:
    """Retry settings (defaults follow typesafe-sdk RetryPolicy, with a ~30s budget).

    max_retries: retries after the first attempt (0 disables retries).
    backoff_initial / backoff_max: exponential backoff, doubled per attempt, capped per sleep.
    backoff_jitter: fraction of the delay randomly shaved off (0.25 -> 75-100% of base).
    total_budget: wall-clock seconds across all attempts; no new sleep starts past it.
    """

    max_retries: int = 4
    backoff_initial: float = 0.5
    backoff_max: float = 5.0
    backoff_jitter: float = 0.25
    total_budget: float = 30.0
    statuses: frozenset[int] = field(default_factory=lambda: RETRY_STATUSES)
    respect_retry_after: bool = True

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.backoff_initial < 0 or self.backoff_max < 0:
            raise ValueError("backoff values must be >= 0")
        if not 0 <= self.backoff_jitter <= 1:
            raise ValueError("backoff_jitter must be between 0 and 1")
        if self.total_budget < 0:
            raise ValueError("total_budget must be >= 0")

    @classmethod
    def disabled(cls) -> "RetryPolicy":
        return cls(max_retries=0)

    def backoff(self, retry_index: int, rng: Callable[[], float] = random.random) -> float:
        """Jittered exponential delay for retry number retry_index (0-based), <= backoff_max."""
        base = min(self.backoff_max, self.backoff_initial * (2**retry_index))
        return max(0.0, base * (1 - self.backoff_jitter * rng()))


def parse_retry_after(headers: Mapping[str, str] | Any, *, now: float | None = None) -> float | None:
    """Seconds to wait from `retry-after-ms`, or `Retry-After` (seconds or HTTP date).

    Returns None when absent or unparseable. Negative values clamp to 0.
    """
    if headers is None:
        return None

    def get(name: str) -> str | None:
        try:
            val = headers.get(name)
        except AttributeError:
            return None
        if val is None and hasattr(headers, "items"):
            for k, v in headers.items():
                if str(k).lower() == name.lower():
                    return v
        return val

    ms = get("retry-after-ms")
    if ms is not None:
        try:
            return max(0.0, float(str(ms).strip()) / 1000.0)
        except ValueError:
            pass

    ra = get("retry-after")
    if ra is None:
        return None
    ra = str(ra).strip()
    try:
        return max(0.0, float(ra))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(ra)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    current = time.time() if now is None else now
    return max(0.0, when.timestamp() - current)


def resolve_api_key(explicit: str | None = None) -> str:
    """Resolve API key from flag, env, or local config file. Never logs the value."""
    if explicit:
        return explicit.strip()

    for name in ("TYPESAFE_API_KEY", "JEV_API_KEY"):
        val = os.environ.get(name)
        if val and val.strip():
            return val.strip()

    # Local-only paths (not in repo)
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


_TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    urllib.error.URLError,  # DNS / connect failures (HTTPError handled separately)
    ConnectionError,  # reset / refused / aborted
    TimeoutError,
    socket.timeout,
    http.client.RemoteDisconnected,
    http.client.IncompleteRead,
)


class JevClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
        retry: RetryPolicy | None = None,
        on_retry: Callable[[int, float, str], None] | None = None,
        _urlopen: Callable[..., Any] | None = None,
        _sleep: Callable[[float], None] | None = None,
        _clock: Callable[[], float] | None = None,
        _rng: Callable[[], float] | None = None,
    ):
        self.api_key = resolve_api_key(api_key)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retry = retry if retry is not None else RetryPolicy()
        self.on_retry = on_retry
        self._urlopen = _urlopen or urllib.request.urlopen
        self._sleep = _sleep or time.sleep
        self._clock = _clock or time.monotonic
        self._rng = _rng or random.random
        self.last_attempts = 0
        self.last_headers: dict[str, str] = {}

    def _build(self, method: str, path: str, payload: dict[str, Any] | None) -> urllib.request.Request:
        url = f"{self.base_url}{path}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", USER_AGENT)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        return req

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        policy = self.retry
        start = self._clock()
        attempt = 0
        while True:
            attempt += 1
            self.last_attempts = attempt
            req = self._build(method, path, payload)
            retry_after: float | None = None
            try:
                with self._urlopen(req, timeout=self.timeout) as resp:
                    try:
                        self.last_headers = {k.lower(): v for k, v in resp.headers.items()}
                    except Exception:
                        self.last_headers = {}
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                err_body: Any
                try:
                    err_body = json.loads(e.read().decode("utf-8"))
                except Exception:
                    err_body = None
                error = JevError(f"HTTP {e.code} from {path}", status=e.code, body=err_body, attempts=attempt)
                if e.code not in policy.statuses:
                    raise error from e
                if policy.respect_retry_after:
                    retry_after = parse_retry_after(e.headers)
                reason = f"HTTP {e.code}"
                cause: BaseException = e
            except _TRANSIENT_ERRORS as e:
                error = JevError(f"Network error calling {path}: {e}", attempts=attempt)
                reason = type(e).__name__
                cause = e

            # Decide whether to retry.
            retries_done = attempt - 1
            if retries_done >= policy.max_retries:
                if policy.max_retries:
                    error.args = (f"{error.args[0]} (gave up after {attempt} attempts)",)
                raise error from cause
            delay = policy.backoff(retries_done, self._rng)
            if retry_after is not None:
                delay = retry_after
            elapsed = self._clock() - start
            if elapsed + delay > policy.total_budget:
                error.args = (
                    f"{error.args[0]} (retry budget of {policy.total_budget:g}s exhausted "
                    f"after {attempt} attempt(s))",
                )
                raise error from cause
            if self.on_retry:
                self.on_retry(attempt, delay, reason)
            self._sleep(delay)

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
