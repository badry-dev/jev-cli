import json
import unittest
from datetime import date
from pathlib import Path

from jev_cli.validation import (
    ValidationError,
    estimate_cost_usd,
    estimate_tokens,
    format_usd,
    noul_confidence,
    parse_release_date,
    size_warnings,
    validate_request,
)

NOUL = {"type": "noul", "instructions": "Is it urgent?"}


class ValidationTests(unittest.TestCase):
    def assertProblem(self, state, questions, fragment):
        with self.assertRaises(ValidationError) as ctx:
            validate_request(state, questions)
        joined = " | ".join(ctx.exception.problems)
        self.assertIn(fragment, joined)

    def test_valid_example_request(self):
        req = json.loads(
            (Path(__file__).resolve().parents[1] / "examples" / "ticket.request.json").read_text()
        )
        out = validate_request(req["state"], req["questions"])
        self.assertEqual(out, req["questions"])

    def test_state_null(self):
        self.assertProblem(None, {"q": NOUL}, "state: must not be null")

    def test_state_empty_string(self):
        self.assertProblem("   ", {"q": NOUL}, "state: must not be empty")

    def test_state_wrong_type(self):
        self.assertProblem(42, {"q": NOUL}, "state: must be a string, object, or array")

    def test_questions_empty_or_wrong_type(self):
        self.assertProblem("x", {}, "at least one question")
        self.assertProblem("x", [NOUL], "questions: must be a JSON object map")

    def test_unknown_type(self):
        self.assertProblem("x", {"q": {"type": "yesno", "instructions": "?"}}, "questions.q.type")

    def test_choice_missing_and_empty(self):
        self.assertProblem("x", {"q": {"type": "choice", "instructions": "?"}}, "need criteria")
        self.assertProblem("x", {"q": {"type": "choice", "criteria": {}}}, "must not be empty")
        self.assertProblem("x", {"q": {"type": "choice", "criteria": []}}, "must not be empty")
        self.assertProblem("x", {"q": {"type": "choice", "criteria": "a,b"}}, "must be an object")

    def test_choice_max_options(self):
        ok = {f"o{i}": None for i in range(255)}
        validate_request("x", {"q": {"type": "choice", "criteria": ok}})
        too_many = {f"o{i}": None for i in range(256)}
        self.assertProblem("x", {"q": {"type": "choice", "criteria": too_many}}, "at most 255")
        self.assertProblem(
            "x", {"q": {"type": "choice", "criteria": [f"o{i}" for i in range(256)]}}, "at most 255"
        )

    def test_choice_list_normalized_to_map(self):
        out = validate_request("x", {"q": {"type": "choice", "criteria": ["a", "b"]}})
        self.assertEqual(out["q"]["criteria"], {"a": None, "b": None})
        self.assertProblem("x", {"q": {"type": "choice", "criteria": ["a", "a"]}}, "unique")

    def test_score_level_counts(self):
        validate_request("x", {"q": {"type": "score", "criteria": ["lo", "hi"]}})
        validate_request("x", {"q": {"type": "score", "criteria": [str(i) for i in range(10)]}})
        self.assertProblem("x", {"q": {"type": "score", "criteria": ["only"]}}, "2-10 levels")
        self.assertProblem(
            "x", {"q": {"type": "score", "criteria": [str(i) for i in range(11)]}}, "2-10 levels"
        )
        self.assertProblem("x", {"q": {"type": "score"}}, "ordered list")

    def test_score_old_dict_form_rejected_with_hint(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_request("x", {"q": {"type": "score", "criteria": {"1": "mid", "0": "low", "2": "high"}}})
        msg = ctx.exception.problems[0]
        self.assertIn("dict-keyed-by-integers", msg)
        self.assertIn('["low", "mid", "high"]', msg)

    def test_score_other_dict_rejected(self):
        self.assertProblem("x", {"q": {"type": "score", "criteria": {"low": 1}}}, "ordered list")

    def test_noul_needs_instructions_or_criteria(self):
        self.assertProblem("x", {"q": {"type": "noul"}}, "need instructions or criteria")
        self.assertProblem("x", {"q": {"type": "noul", "instructions": " "}}, "need instructions or criteria")
        validate_request("x", {"q": {"type": "noul", "criteria": {"true": "yes"}}})
        self.assertProblem(
            "x", {"q": {"type": "noul", "instructions": "?", "criteria": {"maybe": "x"}}}, "unknown key"
        )

    def test_collects_multiple_problems(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_request(None, {"a": {"type": "noul"}, "b": {"type": "score", "criteria": ["x"]}})
        self.assertEqual(len(ctx.exception.problems), 3)

    def test_input_not_mutated(self):
        qs = {"q": {"type": "choice", "criteria": ["a", "b"]}}
        validate_request("x", qs)
        self.assertEqual(qs["q"]["criteria"], ["a", "b"])


class SizeAndCostTests(unittest.TestCase):
    def test_estimate_tokens(self):
        self.assertEqual(estimate_tokens("abcd"), 1)
        self.assertEqual(estimate_tokens("abcde"), 2)
        self.assertEqual(estimate_tokens({"a": 1}), 2)  # '{"a": 1}' = 8 chars

    def test_no_warning_for_small(self):
        self.assertEqual(size_warnings("hello", {"q": NOUL}), [])

    def test_warns_near_pair_limit(self):
        state = "x" * (27_000 * 4)
        w = size_warnings(state, {"q": NOUL})
        self.assertEqual(len(w), 1)
        self.assertIn("state + longest question", w[0])
        self.assertIn("estimate", w[0])
        self.assertIn("close to", w[0])

    def test_warns_exceeding_both(self):
        state = "x" * (70_000 * 4)
        w = size_warnings(state, {"q": NOUL})
        self.assertEqual(len(w), 2)
        self.assertTrue(all("exceeds" in m for m in w))

    def test_total_limit_with_many_questions(self):
        state = "x" * (20_000 * 4)
        qs = {f"q{i}": {"type": "noul", "instructions": "y" * 4000} for i in range(45)}
        w = size_warnings(state, qs)
        self.assertTrue(any("state + all questions" in m for m in w))

    def test_cost(self):
        self.assertAlmostEqual(estimate_cost_usd(1_000_000), 0.042)
        self.assertAlmostEqual(estimate_cost_usd(272), 272 * 0.042 / 1e6)
        self.assertEqual(estimate_cost_usd(0), 0.0)
        self.assertIsNone(estimate_cost_usd(None))
        self.assertIsNone(estimate_cost_usd("abc"))
        self.assertIsNone(estimate_cost_usd(-5))

    def test_format_usd(self):
        self.assertEqual(format_usd(0.042), "$0.0420")
        self.assertEqual(format_usd(272 * 0.042 / 1e6), "$0.00001142")
        self.assertEqual(format_usd(None), "n/a")
        self.assertEqual(format_usd(0), "$0")

    def test_noul_confidence(self):
        self.assertEqual(noul_confidence(0.5), 0.0)
        self.assertEqual(noul_confidence(0.95), 0.9)
        self.assertEqual(noul_confidence(0.0), 1.0)
        self.assertIsNone(noul_confidence(1.5))
        self.assertIsNone(noul_confidence("x"))

    def test_parse_release_date(self):
        self.assertEqual(parse_release_date("2026-09-15"), date(2026, 9, 15))
        self.assertEqual(parse_release_date("2026-09-15T12:00:00Z"), date(2026, 9, 15))
        self.assertEqual(parse_release_date("2026-09-15T12:00:00.123+03:00"), date(2026, 9, 15))
        self.assertEqual(parse_release_date("2026-09-15 garbage"), date(2026, 9, 15))
        self.assertIsNone(parse_release_date("soon"))
        self.assertIsNone(parse_release_date(None))


if __name__ == "__main__":
    unittest.main()
