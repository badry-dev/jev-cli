import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from jev_cli import cli
from jev_cli import usage as usage_log
from jev_cli.client import JevError

SECRET = "sk-test-secret-should-never-be-logged"
QUESTIONS = {
    "route": {"type": "choice", "instructions": "Where?", "criteria": {"a": "A", "b": "B", "c": "C"}},
    "level": {"type": "score", "instructions": "How?", "criteria": ["low", "mid", "high"]},
    "go": {"type": "noul", "instructions": "Go?"},
}
RESULT = {
    "model": "jev-1.13.0",
    "answers": {
        "route": {"type": "choice", "choice": "b", "confidence": 0.42, "probabilities": {"a": 0.1, "b": 0.6, "c": 0.3}},
        "level": {"type": "score", "score": 1.6, "confidence": 0.7},
        "go": {"type": "noul", "noul": 0.9},
    },
    "usage": {"input_tokens": 1000, "output_tokens": 30},
}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "nested" / "dir" / "usage.jsonl"
        env = {k: v for k, v in os.environ.items() if k not in ("JEV_NO_LOG", "JEV_USAGE_LOG")}
        env["JEV_USAGE_LOG"] = str(self.log)
        env["TYPESAFE_API_KEY"] = SECRET
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def run_cli(self, argv, result=RESULT, side_effect=None, attempts=1):
        fake = mock.MagicMock()
        if side_effect is not None:
            fake.evaluate.side_effect = side_effect
        else:
            fake.evaluate.return_value = result
        fake.last_attempts = attempts
        with mock.patch("jev_cli.cli.JevClient", return_value=fake) as cls, \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            rc = cli.main(argv)
        return rc, fake, cls, out.getvalue(), err.getvalue()

    def lines(self):
        if not self.log.exists():
            return []
        return [json.loads(l) for l in self.log.read_text().splitlines() if l.strip()]


class LogWritingTests(_Base):
    def test_ask_appends_one_line_with_expected_fields(self):
        rc, *_ = self.run_cli([
            "--quiet", "ask", "-m", "jev-1.13.0", "-s", "secret state text", "-q", json.dumps(QUESTIONS),
            "--caller", "Max Grok", "--purpose", "Pick a draft", "--pattern", "intent_routing+confidence_gate",
        ], attempts=2)
        self.assertEqual(rc, 0)
        entries = self.lines()
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["caller"], "Max Grok")
        self.assertEqual(e["purpose"], "Pick a draft")
        self.assertEqual(e["pattern"], "intent_routing+confidence_gate")
        self.assertEqual(e["model_requested"], "jev-1.13.0")
        self.assertEqual(e["model_answered"], "jev-1.13.0")
        self.assertEqual(e["status"], "ok")
        self.assertEqual(e["input_tokens"], 1000)
        self.assertEqual(e["output_tokens"], 30)
        self.assertAlmostEqual(e["est_cost_usd"], 1000 * 0.042 / 1e6)
        self.assertEqual(e["retries"], 1)
        self.assertIsInstance(e["latency_ms"], int)
        self.assertEqual(
            e["questions"],
            [{"id": "route", "type": "choice", "n_options": 3},
             {"id": "level", "type": "score", "n_options": 3},
             {"id": "go", "type": "noul", "n_options": None}],
        )
        self.assertEqual(e["answers"]["route"], {"type": "choice", "choice": "b", "confidence": 0.42})
        self.assertEqual(e["answers"]["level"], {"type": "score", "score": 1.6, "confidence": 0.7})
        self.assertEqual(e["answers"]["go"], {"type": "noul", "p": 0.9})
        self.assertEqual(len(e["request_sha256"]), 64)
        self.assertIsNotNone(datetime.fromisoformat(e["ts"]).tzinfo)

    def test_log_is_private(self):
        self.run_cli(["--quiet", "ask", "-s", "very private state", "-q", json.dumps(QUESTIONS)])
        raw = self.log.read_text()
        self.assertNotIn(SECRET, raw)
        self.assertNotIn("Bearer", raw)
        self.assertNotIn("Authorization", raw)
        self.assertNotIn("very private state", raw)
        self.assertNotIn("Where?", raw)

    def test_http_error_is_logged_and_still_raised(self):
        err = JevError("HTTP 529 from /v1/systemone (gave up after 5 attempts)", status=529, attempts=5)
        rc, *_ = self.run_cli(["--quiet", "ask", "-s", "x", "-q", json.dumps(QUESTIONS)], side_effect=err)
        self.assertEqual(rc, 1)
        e = self.lines()[0]
        self.assertEqual(e["status"], 529)
        self.assertEqual(e["retries"], 4)
        self.assertIsNone(e["input_tokens"])

    def test_validation_error_is_logged_without_network(self):
        bad = {"u": {"type": "score", "criteria": {"0": "a", "1": "b"}}}
        rc, _fake, cls, _out, _err = self.run_cli(["ask", "-s", "x", "-q", json.dumps(bad)])
        self.assertEqual(rc, 1)
        cls.assert_not_called()
        self.assertEqual(self.lines()[0]["status"], "validation_error")

    def test_log_write_failure_does_not_fail_call(self):
        blocker = Path(self.tmp.name) / "afile"
        blocker.write_text("x")
        os.environ["JEV_USAGE_LOG"] = str(blocker / "sub" / "usage.jsonl")  # parent is a file
        rc, _f, _c, out, err = self.run_cli(["--quiet", "ask", "-s", "x", "-q", json.dumps(QUESTIONS)])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), RESULT)
        self.assertIn("could not write usage log", err)

    def test_creates_parent_dirs(self):
        self.assertFalse(self.log.parent.exists())
        self.run_cli(["--quiet", "ask", "-s", "x", "-q", json.dumps(QUESTIONS)])
        self.assertTrue(self.log.exists())


class NoLogTests(_Base):
    def test_no_log_flag(self):
        rc, *_ = self.run_cli(["--quiet", "ask", "--no-log", "-s", "x", "-q", json.dumps(QUESTIONS)])
        self.assertEqual(rc, 0)
        self.assertFalse(self.log.exists())

    def test_no_log_env(self):
        os.environ["JEV_NO_LOG"] = "1"
        rc, *_ = self.run_cli(["--quiet", "ask", "-s", "x", "-q", json.dumps(QUESTIONS)])
        self.assertEqual(rc, 0)
        self.assertFalse(self.log.exists())

    def test_no_log_env_falsey_still_logs(self):
        os.environ["JEV_NO_LOG"] = "0"
        self.run_cli(["--quiet", "ask", "-s", "x", "-q", json.dumps(QUESTIONS)])
        self.assertEqual(len(self.lines()), 1)


class MetaTests(_Base):
    def _request_file(self, meta):
        req = {"model": "jev-1.13.0", "state": {"k": "v"}, "questions": QUESTIONS, "_meta": meta}
        path = Path(self.tmp.name) / "req.json"
        path.write_text(json.dumps(req))
        return str(path)

    def test_meta_is_stripped_before_sending_and_logged(self):
        path = self._request_file({"caller": "Max Grok", "purpose": "Gate a plan", "pattern": ["confidence_gate"]})
        rc, fake, *_ = self.run_cli(["--quiet", "ask", "-r", path])
        self.assertEqual(rc, 0)
        kwargs = fake.evaluate.call_args.kwargs
        self.assertEqual(set(kwargs), {"state", "questions", "model"})
        self.assertNotIn("_meta", json.dumps(kwargs))
        e = self.lines()[0]
        self.assertEqual((e["caller"], e["purpose"], e["pattern"]), ("Max Grok", "Gate a plan", "confidence_gate"))
        self.assertEqual(
            e["request_sha256"],
            usage_log.request_sha256({"model": "jev-1.13.0", "state": {"k": "v"}, "questions": QUESTIONS}),
        )

    def test_flags_override_meta(self):
        path = self._request_file({"caller": "File", "purpose": "file purpose", "pattern": "urgency"})
        self.run_cli(["--quiet", "ask", "-r", path, "--caller", "Flag", "--pattern", "guardrail"])
        e = self.lines()[0]
        self.assertEqual((e["caller"], e["purpose"], e["pattern"]), ("Flag", "file purpose", "guardrail"))

    def test_unknown_pattern_warns_but_logs(self):
        _rc, _f, _c, _o, err = self.run_cli(
            ["--quiet", "ask", "-s", "x", "-q", json.dumps(QUESTIONS), "--pattern", "vibes"]
        )
        self.assertIn("unknown pattern", err)
        self.assertEqual(self.lines()[0]["pattern"], "vibes")

    def test_normalize_pattern(self):
        self.assertEqual(usage_log.normalize_pattern("a, b"), "a+b")
        self.assertEqual(usage_log.normalize_pattern(["a", "b"]), "a+b")
        self.assertIsNone(usage_log.normalize_pattern(""))
        self.assertIsNone(usage_log.normalize_pattern(None))


TZ = timezone(timedelta(hours=3))


def _entry(ts, caller="Max Grok", pattern="confidence_gate", status="ok", inp=1000, out=10, answers=None, **kw):
    e = {
        "ts": ts.isoformat(timespec="seconds"), "caller": caller, "purpose": "p", "pattern": pattern,
        "model_answered": "jev-1.13.0" if status == "ok" else None, "status": status,
        "input_tokens": inp if status == "ok" else None, "output_tokens": out if status == "ok" else None,
        "est_cost_usd": inp * 0.042 / 1e6 if status == "ok" else None, "retries": 0, "latency_ms": 100,
        "answers": answers or {},
    }
    e.update(kw)
    return e


class AggregationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 10, 12, 0, tzinfo=TZ)
        self.entries = [
            _entry(self.now - timedelta(days=1), answers={"a": {"type": "choice", "choice": "x", "confidence": 0.4}}),
            _entry(self.now - timedelta(days=2), caller="Router", pattern="intent_routing+guardrail",
                   answers={"s": {"type": "score", "score": 1.5, "confidence": 0.9},
                            "n": {"type": "noul", "p": 0.95}}, backfilled=True),
            _entry(self.now - timedelta(days=3), status=429, retries=2),
            _entry(self.now - timedelta(days=10), inp=5000),  # outside 7 days
        ]

    def test_summary_last_7_days(self):
        start = usage_log.since_from(days=7, now=self.now)
        s = usage_log.summarize(self.entries, start=start, end=self.now)
        t = s["totals"]
        self.assertEqual((t["calls"], t["ok"], t["errors"]), (3, 2, 1))
        self.assertEqual(t["input_tokens"], 2000)
        self.assertAlmostEqual(t["est_cost_usd"], 2000 * 0.042 / 1e6)
        self.assertEqual(s["by_caller"]["Max Grok"]["calls"], 2)
        self.assertEqual(s["by_caller"]["Router"]["calls"], 1)
        self.assertEqual(s["by_pattern"]["confidence_gate"]["calls"], 2)
        self.assertEqual(s["by_pattern"]["intent_routing"]["calls"], 1)
        self.assertEqual(s["by_pattern"]["guardrail"]["calls"], 1)
        self.assertEqual(s["statuses"], {"ok": 2, "429": 1})
        self.assertEqual(s["backfilled_calls"], 1)
        c = s["confidence"]
        self.assertEqual(c["answers"], 3)  # 0.4, 0.9, |2*0.95-1| = 0.9
        self.assertAlmostEqual(c["min"], 0.4)
        self.assertAlmostEqual(c["avg"], (0.4 + 0.9 + 0.9) / 3, places=4)
        self.assertEqual(c["low_count"], 1)
        self.assertEqual(c["low"][0]["question"], "a")

    def test_since_date(self):
        start = usage_log.since_from(since="2026-09-30", now=self.now)
        s = usage_log.summarize(self.entries, start=start, end=self.now)
        self.assertEqual(s["totals"]["calls"], 4)

    def test_empty(self):
        s = usage_log.summarize([], start=self.now - timedelta(days=7), end=self.now)
        self.assertEqual(s["totals"]["calls"], 0)
        self.assertIsNone(s["confidence"]["avg"])
        self.assertIn("no answers", usage_log.format_summary(s))

    def test_usage_command_text_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "u.jsonl"
            now = datetime.now().astimezone()
            rows = [_entry(now - timedelta(hours=1), answers={"a": {"type": "choice", "choice": "x", "confidence": 0.3}}),
                    _entry(now - timedelta(days=20))]
            log.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
                    mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                rc = cli.main(["usage", "--log", str(log), "--json"])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())
            self.assertEqual(data["totals"]["calls"], 1)
            self.assertEqual(data["confidence"]["low_count"], 1)
            self.assertIn("skipped 1 unreadable", err.getvalue())
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
                    mock.patch("sys.stderr", new_callable=io.StringIO):
                rc = cli.main(["usage", "--log", str(log), "--days", "30"])
            self.assertEqual(rc, 0)
            self.assertIn("calls: 2", out.getvalue())
            self.assertIn("by caller", out.getvalue())

    def test_usage_missing_log_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cli.main(["usage", "--log", str(Path(tmp) / "none.jsonl")])
        self.assertEqual(rc, 0)
        self.assertIn("calls: 0", out.getvalue())

    def test_bad_since(self):
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(cli.main(["usage", "--since", "10/01/2026"]), 1)


if __name__ == "__main__":
    unittest.main()
