import json
from pathlib import Path

import pytest

from jev_cli.client import JevError, resolve_api_key


def test_resolve_api_key_from_env(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-123")
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    assert resolve_api_key() == "test-key-123"


def test_resolve_api_key_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(JevError):
        resolve_api_key()


def test_examples_are_valid_json():
    root = Path(__file__).resolve().parents[1] / "examples"
    for path in root.glob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))
