import json
import os
import unittest
from pathlib import Path
from unittest import mock

from jev_cli.client import JevError, resolve_api_key


class ResolveApiKeyTests(unittest.TestCase):
    def test_resolve_api_key_from_env(self):
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key-123"}, clear=False):
            os.environ.pop("JEV_API_KEY", None)
            self.assertEqual(resolve_api_key(), "test-key-123")

    def test_resolve_api_key_missing(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if k not in ("TYPESAFE_API_KEY", "JEV_API_KEY")}
            env["HOME"] = tmp  # empty home so the file fallback misses
            with mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaises(JevError):
                    resolve_api_key()


class ExamplesTests(unittest.TestCase):
    def test_examples_are_valid_json(self):
        root = Path(__file__).resolve().parents[1] / "examples"
        for path in root.glob("*.json"):
            json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
