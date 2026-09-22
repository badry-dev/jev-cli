# jev-cli

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

## Question types

Matches the System One HTTP API:

- `noul` — yes/no probability in `[0, 1]`
- `choice` — one option from a criteria map
- `score` — position on ordered criteria levels

See `skills/typesafe-ai/SKILL.md` and https://docs.typesafe.ai/llms.txt.

## Repo layout

```
src/jev_cli/          # CLI + HTTP client
examples/             # Sample requests (no secrets)
skills/typesafe-ai/   # TypeSafe agent skill (vendored)
docs/                 # Notes for this project
```

## License

MIT
