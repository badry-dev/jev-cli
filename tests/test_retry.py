import email.message
import io
import json
import unittest
import urllib.error
from email.utils import format_datetime
from datetime import datetime, timezone
from unittest import mock

from jev_cli.client import USER_AGENT, JevClient, JevError, RetryPolicy, parse_retry_after


def _headers(**kv):
    msg = email.message.Message()
    for k, v in kv.items():
        msg[k.replace("_", "-")] = v
    return msg


def _http_error(code, headers=None, body=b'{"detail":"x"}'):
    return urllib.error.HTTPError(
        "https://api.typesafe.ai/v1/systemone", code, "err", headers or _headers(), io.BytesIO(body)
    )


class _Resp:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode()
        self.headers = _headers(x_typesafe_request_id="req_1")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


OK = {"model": "jev-1.13.0", "answers": {}, "usage": {"input_tokens": 10, "output_tokens": 1}}


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def make_client(responses, policy=None, rng=lambda: 0.0):
    """responses: list of exceptions or payload dicts, consumed in order."""
    fc = FakeClock()
    calls = []

    def urlopen(req, timeout):
        calls.append(req)
        item = responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return _Resp(item)

    client = JevClient(
        api_key="test-key",
        retry=policy or RetryPolicy(),
        _urlopen=urlopen,
        _sleep=fc.sleep,
        _clock=fc.clock,
        _rng=rng,
    )
    return client, fc, calls


class RetryAfterParsingTests(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(parse_retry_after(_headers(retry_after="3")), 3.0)
        self.assertEqual(parse_retry_after(_headers(retry_after="1.5")), 1.5)

    def test_ms_takes_precedence(self):
        self.assertEqual(parse_retry_after(_headers(retry_after="9", retry_after_ms="250")), 0.25)

    def test_http_date(self):
        now = datetime(2026, 10, 9, 10, 0, 0, tzinfo=timezone.utc)
        later = datetime(2026, 10, 9, 10, 0, 4, tzinfo=timezone.utc)
        hdr = _headers(retry_after=format_datetime(later, usegmt=True))
        self.assertAlmostEqual(parse_retry_after(hdr, now=now.timestamp()), 4.0)

    def test_past_date_and_negative_clamp(self):
        now = datetime(2026, 10, 9, 10, 0, 0, tzinfo=timezone.utc)
        past = datetime(2026, 10, 9, 9, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(parse_retry_after(_headers(retry_after=format_datetime(past, usegmt=True)), now=now.timestamp()), 0.0)
        self.assertEqual(parse_retry_after(_headers(retry_after="-2")), 0.0)

    def test_missing_or_garbage(self):
        self.assertIsNone(parse_retry_after(_headers()))
        self.assertIsNone(parse_retry_after(_headers(retry_after="soon")))
        self.assertIsNone(parse_retry_after(None))
        self.assertEqual(parse_retry_after({"Retry-After": "2"}), 2.0)  # plain dict, any case


class BackoffTests(unittest.TestCase):
    def test_exponential_and_capped(self):
        p = RetryPolicy(backoff_jitter=0)
        self.assertEqual([p.backoff(i) for i in range(6)], [0.5, 1.0, 2.0, 4.0, 5.0, 5.0])

    def test_jitter_shaves_at_most_25_percent(self):
        p = RetryPolicy()
        self.assertEqual(p.backoff(0, rng=lambda: 0.0), 0.5)
        self.assertAlmostEqual(p.backoff(0, rng=lambda: 1.0), 0.375)
        self.assertLessEqual(p.backoff(10, rng=lambda: 0.0), 5.0)

    def test_invalid_policy(self):
        with self.assertRaises(ValueError):
            RetryPolicy(max_retries=-1)
        with self.assertRaises(ValueError):
            RetryPolicy(backoff_jitter=2)


class RetryLoopTests(unittest.TestCase):
    def test_retries_529_then_succeeds(self):
        client, fc, calls = make_client([_http_error(529), _http_error(529), OK])
        self.assertEqual(client.models(), OK)
        self.assertEqual(len(calls), 3)
        self.assertEqual(fc.sleeps, [0.5, 1.0])
        self.assertEqual(client.last_attempts, 3)

    def test_retryable_statuses(self):
        for code in (408, 429, 500, 502, 503, 504, 529):
            client, fc, calls = make_client([_http_error(code), OK])
            self.assertEqual(client.models(), OK, code)
            self.assertEqual(len(calls), 2, code)

    def test_non_retryable_statuses(self):
        for code in (400, 401, 403, 404, 422):
            client, fc, calls = make_client([_http_error(code), OK])
            with self.assertRaises(JevError) as ctx:
                client.models()
            self.assertEqual(ctx.exception.status, code)
            self.assertEqual(len(calls), 1, code)
            self.assertEqual(fc.sleeps, [])

    def test_honors_retry_after(self):
        client, fc, _ = make_client([_http_error(429, _headers(retry_after="3")), OK])
        client.models()
        self.assertEqual(fc.sleeps, [3.0])

    def test_honors_retry_after_ms(self):
        client, fc, _ = make_client([_http_error(429, _headers(retry_after_ms="120")), OK])
        client.models()
        self.assertEqual(fc.sleeps, [0.12])

    def test_retry_after_beyond_budget_gives_up(self):
        client, fc, calls = make_client([_http_error(429, _headers(retry_after="60")), OK])
        with self.assertRaises(JevError) as ctx:
            client.models()
        self.assertIn("budget", str(ctx.exception))
        self.assertEqual(len(calls), 1)
        self.assertEqual(fc.sleeps, [])

    def test_gives_up_after_max_retries(self):
        client, fc, calls = make_client([_http_error(503)] * 5, RetryPolicy(max_retries=2))
        with self.assertRaises(JevError) as ctx:
            client.models()
        self.assertEqual(ctx.exception.status, 503)
        self.assertIn("gave up after 3 attempts", str(ctx.exception))
        self.assertEqual(len(calls), 3)

    def test_total_budget_limits_sleeps(self):
        # Defaults: sleeps 0.5,1,2,4,5,5,... ; budget 8s allows 0.5+1+2+4 = 7.5
        client, fc, calls = make_client([_http_error(529)] * 20, RetryPolicy(max_retries=10, total_budget=8))
        with self.assertRaises(JevError):
            client.models()
        self.assertEqual(fc.sleeps, [0.5, 1.0, 2.0, 4.0])
        self.assertLessEqual(sum(fc.sleeps), 8)

    def test_default_budget_about_30s(self):
        client, fc, _ = make_client([_http_error(529)] * 50, RetryPolicy(max_retries=50))
        with self.assertRaises(JevError):
            client.models()
        self.assertLessEqual(sum(fc.sleeps), 30.0)
        self.assertTrue(all(s <= 5.0 for s in fc.sleeps))

    def test_no_retry(self):
        client, fc, calls = make_client([_http_error(529), OK], RetryPolicy.disabled())
        with self.assertRaises(JevError):
            client.models()
        self.assertEqual(len(calls), 1)

    def test_network_errors_retry(self):
        errs = [
            urllib.error.URLError("dns fail"),
            ConnectionResetError("reset"),
            TimeoutError("slow"),
        ]
        client, fc, calls = make_client(errs + [OK])
        self.assertEqual(client.models(), OK)
        self.assertEqual(len(calls), 4)

    def test_network_error_exhausted(self):
        client, fc, calls = make_client([urllib.error.URLError("down")] * 3, RetryPolicy(max_retries=2))
        with self.assertRaises(JevError) as ctx:
            client.models()
        self.assertIn("Network error", str(ctx.exception))
        self.assertIsNone(ctx.exception.status)

    def test_on_retry_callback_and_user_agent(self):
        seen = []
        client, fc, calls = make_client([_http_error(529), OK])
        client.on_retry = lambda a, d, r: seen.append((a, d, r))
        client.evaluate(state="x", questions={"q": {"type": "noul", "instructions": "?"}})
        self.assertEqual(seen, [(1, 0.5, "HTTP 529")])
        self.assertEqual(calls[0].get_header("User-agent"), USER_AGENT)
        self.assertTrue(USER_AGENT.startswith("jev-cli/"))
        # Same body re-sent on retry
        self.assertEqual(calls[0].data, calls[1].data)

    def test_error_body_parsed(self):
        client, _, _ = make_client([_http_error(422, body=b'{"detail":[{"loc":["body"]}]}')])
        with self.assertRaises(JevError) as ctx:
            client.models()
        self.assertEqual(ctx.exception.body, {"detail": [{"loc": ["body"]}]})


class CliValidationTests(unittest.TestCase):
    def test_invalid_request_exits_nonzero_without_network(self):
        from jev_cli import cli

        with mock.patch("jev_cli.cli.JevClient") as client_cls, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            rc = cli.main([
                "ask", "--state", "x",
                "--questions", '{"u": {"type": "score", "criteria": {"0": "a", "1": "b"}}}',
            ])
        self.assertEqual(rc, 1)
        client_cls.assert_not_called()
        payload = json.loads(err.getvalue())
        self.assertIn("pre-send validation", payload["error"])
        self.assertIn("dict-keyed-by-integers", payload["body"]["problems"][0])

    def test_success_keeps_stdout_pure_json(self):
        from jev_cli import cli

        fake = mock.MagicMock()
        fake.evaluate.return_value = {
            "model": "jev-1.13.0",
            "answers": {"q": {"type": "noul", "noul": 0.9}},
            "usage": {"input_tokens": 1000, "output_tokens": 20},
        }
        fake.last_attempts = 1
        with mock.patch("jev_cli.cli.JevClient", return_value=fake), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            rc = cli.main(["ask", "-s", "x", "-q", '{"q": {"type": "noul", "instructions": "?"}}'])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue()), fake.evaluate.return_value)
        self.assertIn("model=jev-1.13.0", err.getvalue())
        self.assertIn("est_cost=$0.000042", err.getvalue())
        self.assertIn("q=0.80", err.getvalue())

    def test_annotate_and_model_pin(self):
        from jev_cli import cli

        fake = mock.MagicMock()
        fake.evaluate.return_value = {"model": "jev-1.13.0", "answers": {}, "usage": {"input_tokens": 100}}
        fake.last_attempts = 2
        with mock.patch("jev_cli.cli.JevClient", return_value=fake), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
                mock.patch("sys.stderr", new_callable=io.StringIO):
            rc = cli.main(["--quiet", "ask", "-m", "jev-1.13.0", "--annotate", "-s", "x",
                           "-q", '{"q": {"type": "noul", "instructions": "?"}}'])
        self.assertEqual(rc, 0)
        self.assertEqual(fake.evaluate.call_args.kwargs["model"], "jev-1.13.0")
        data = json.loads(out.getvalue())
        self.assertEqual(data["_jev"]["attempts"], 2)
        self.assertAlmostEqual(data["_jev"]["est_cost_usd"], 100 * 0.042 / 1e6)


if __name__ == "__main__":
    unittest.main()
