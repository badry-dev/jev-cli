# jev-cli

> **My unofficial CLI - not affiliated with or endorsed by TypeSafe.**
> Bring your own TypeSafe API key; nothing here spends anyone else's credits.

I made this so I can use it mainly with the [Jev router](https://x.ai/bot/zlhrtju3wamiShqOcX3UI)  Grok bot. 

Thin command-line client for **TypeSafe System One (Jev)**.

Calls the HTTP API directly (stdlib only — no SDK required). Secrets stay in
environment variables or a local file; nothing secret is committed here.

| Item | Value |
| --- | --- |
| Evaluate | `POST https://api.typesafe.ai/v1/systemone` |
| Models | `GET https://api.typesafe.ai/v1/models` |
| Auth | `Authorization: Bearer <API_KEY>` |

> TypeSafe also publishes an official Python package [`typesafe-sdk`](https://docs.typesafe.ai/sdk/python).
> This CLI is intentionally zero-dependency so agents and scripts can call Jev without installing that SDK.

## Install

```bash
python3 -m pip install -e .
# or run without install:
PYTHONPATH=src python3 -m jev_cli models
```

## Auth (never commit keys)

Set one of:

```bash
export TYPESAFE_API_KEY='…'   # preferred (matches TypeSafe docs)
export JEV_API_KEY='…'        # also accepted
```

Or write the key to a local file (gitignored paths):

```bash
mkdir -p ~/.config/jev
chmod 700 ~/.config/jev
printf '%s' "$TYPESAFE_API_KEY" > ~/.config/jev/api_key
chmod 600 ~/.config/jev/api_key
```

See `.env.example`. Do **not** put real keys in the repo, issues, or commit messages.

## Usage

List models:

```bash
jev models
```

Ask questions (full request file):

```bash
jev ask --request examples/ticket.request.json
```

Or pass state + questions separately:

```bash
jev ask \
  --state 'Customer: I was charged twice and want a refund today.' \
  --questions examples/ticket.questions.json
```

Structured state:

```bash
jev ask --state-json --state examples/ticket.state.json --questions examples/ticket.questions.json
```

Pin a model version instead of the moving `jev-latest` alias (recommended once
you've tuned thresholds against a version):

```bash
jev ask --model jev-1.13.0 --request examples/ticket.request.json
```

An explicit `--model` overrides the `model` inside a `--request` file; without
the flag the file's model is used, then `jev-latest`.

Models as a quick table (release dates parsed from `YYYY-MM-DD` or full ISO):

```bash
jev models --table
```

## Output: stdout vs stderr

- **stdout** is exactly the API's JSON response, so `jev ask … | jq` keeps working.
  The response's `model` field always reports the versioned model that answered.
- **stderr** gets a one-line summary after each `ask`:

  ```
  jev: model=jev-1.13.0 input_tokens=282 output_tokens=20 est_cost=$0.00001184 (input @ $0.042/1M tok, output free) noul |2p-1|: urgent=0.86
  ```

  The cost is an **estimate**: `usage.input_tokens × $0.042 / 1M` (Jev 1.13 list
  price; output tokens are free). `noul |2p-1|` is a confidence-style distance from
  0.5 for each noul answer (the API itself does not return confidence for nouls).
- `--quiet` / `-Q` (or `JEV_QUIET=1`) hides the summary and retry notes. Warnings
  and errors still print.
- `--annotate` opts in to adding a `_jev` object to the JSON
  (`model`, `input_tokens`, `output_tokens`, `est_cost_usd`, `attempts`, `noul_confidence`).
  Without it, the API's JSON is never modified.

## Pre-send validation

Before any network call, `jev ask` checks the request the same way
`typesafe-sdk` 0.7.3 does and exits `1` with a list of problems (nothing is sent,
nothing is billed):

- `state` must not be null or an empty string, and must be a string, object, or array.
- `questions` must be a non-empty object.
- **choice**: `criteria` must be a non-empty object (option → description), at most
  255 options. A list of option names (`["billing", "technical"]`) is accepted and
  sent as `{"billing": null, "technical": null}`.
- **score**: `criteria` must be an ordered array of 2–10 levels, lowest first. The old
  `{"0": "...", "1": "..."}` form is rejected with the equivalent list in the message.
- **noul**: needs `instructions` or `criteria`; criteria keys may only be `true`/`false`.

`--skip-validation` sends the request as-is.

## Size limits (estimate)

Jev 1.13 accepts 64k tokens per request (state + all questions) and 32k for
state + the longest question. `jev ask` estimates size as JSON characters ÷ 4 and
warns on stderr at 80% of either limit, or when an estimate exceeds one. It's a
rough heuristic, not a tokenizer, so it never blocks the request.

## Retries

Requests retry on `408`, `429`, every `5xx` (including TypeSafe's `529 Overloaded`)
and transient network errors (DNS/connect failures, resets, timeouts). `400`,
`401`, `403`, `404` and `422` never retry.

- Jittered exponential backoff: 0.5s, 1s, 2s, 4s, then capped at 5s per sleep
  (each delay randomly trimmed by up to 25%).
- `Retry-After` (seconds or HTTP date) and `retry-after-ms` headers are honored.
- Up to 4 retries within a ~30s wall-clock budget; no new wait starts if it would
  pass the budget (a `Retry-After` longer than what's left ends the run).
- Each retry is noted on stderr.

| Flag | Env | Default |
| --- | --- | --- |
| `--max-retries N` | `JEV_MAX_RETRIES` | `4` |
| `--retry-budget SECONDS` | `JEV_RETRY_BUDGET` | `30` |
| `--no-retry` | | off |
| `--timeout SECONDS` (per request) | | `60` |

Global flags go before the subcommand: `jev --no-retry ask …`.

Requests send `User-Agent: jev-cli/<version>`.

## Usage log (local)

Every `jev ask` appends one JSON line to a local log, so you can see how often
Jev is called, by whom, for what, at what cost, and how confident the answers were.

| Setting | Default |
| --- | --- |
| Log path | `~/.local/share/jev/usage.jsonl` (override with `JEV_USAGE_LOG=/path/file.jsonl`) |
| Disable for one call | `jev ask --no-log …` |
| Disable everywhere | `JEV_NO_LOG=1` |

Missing directories are created. If the log can't be written, `jev ask` prints a
warning on stderr and still returns the answer. Failed calls are logged too
(`status` is the HTTP code, `network_error`, or `validation_error`).

Tag each call so the log is useful (all optional):

```bash
jev ask -r request.json \
  --caller "Max Grok" \
  --purpose "Gate the weekly plan before writing memory" \
  --pattern confidence_gate+intent_routing
```

`--pattern` is one of `intent_routing`, `confidence_gate`, `urgency`, `guardrail`,
`harness`, `other`; join several with `+`. Unknown names warn but are logged as given.

The same fields can live in a top-level `_meta` object in a `--request` file.
`_meta` is stripped before the request is sent, and flags win over it:

```json
{
  "_meta": {"caller": "Max Grok", "purpose": "Pick tonight's draft", "pattern": "intent_routing"},
  "model": "jev-1.13.0",
  "state": "…",
  "questions": {"…": {}}
}
```

### Privacy: what a log line contains

`ts` (ISO 8601 with offset), `caller`, `purpose`, `pattern`, `model_requested`,
`model_answered`, `status`, `input_tokens`, `output_tokens`, `est_cost_usd`
(input × $0.042 / 1M), `latency_ms`, `retries`, `questions` (id, type and option
count only), `answers` (choice + confidence, score + confidence, or noul `p`), and
`request_sha256` (SHA-256 of the canonical request body, to spot repeats).

It **never** contains the API key, any header, the `state`, or question /
criteria text. `purpose` is whatever you pass, so keep secrets out of it.
The log stays on your machine; nothing is uploaded.

### `jev usage`

```bash
jev usage                 # last 7 days
jev usage --days 14
jev usage --since 2026-10-01
jev usage --json          # machine-readable summary
jev usage --low 0.6       # change the low-confidence threshold (default 0.5)
```

Prints calls (ok vs errors), tokens, estimated cost, retries, average latency,
and breakdowns by caller, pattern and day, plus average / minimum answer
confidence and every answer below the threshold. Choice and score answers use
the API's `confidence`; noul answers use `|2p − 1|`. Calls without `--purpose`
are counted so you can spot untagged usage. `jev usage` needs no API key.

## Account balance

The API has no balance, credits, or usage endpoint (only `POST /v1/systemone` and
`GET /v1/models` exist), and responses carry no credit headers. Check remaining
credit in the TypeSafe console: https://console.typesafe.ai.

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Stdlib only; no network calls (urllib is mocked).

## Question types

Matches the System One HTTP API:

- `noul` — yes/no probability in `[0, 1]`
- `choice` — one option from a criteria map
- `score` — position on ordered criteria levels

See `skills/typesafe-ai/SKILL.md` and https://docs.typesafe.ai/llms.txt.

## Repo layout

```
src/jev_cli/          # CLI, HTTP client (retries), validation/cost helpers, usage log
tests/                # unittest suite (no network)
examples/             # Sample requests (no secrets)
skills/typesafe-ai/   # TypeSafe agent skill (vendored)
docs/                 # Notes for this project
```

## License

MIT. The vendored skill summary in `skills/typesafe-ai/` is © TypeSafe AI under
its own MIT license (`skills/typesafe-ai/LICENSE`).
