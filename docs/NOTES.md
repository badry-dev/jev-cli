# Project notes

## Why a CLI

Jev Router (Grok Bot) needs a reusable, secret-free way to call TypeSafe System One
on the bot computer. One-off curl worked for smoke tests; this package is the
standing caller.

## API shape (verified 2026-09-22)

- Base: `https://api.typesafe.ai`
- `GET /v1/models` → `{ "models": [ { "name", "description", "release_date" }, ... ] }`
- `POST /v1/systemone` body:
  - `model` (e.g. `jev-latest`)
  - `state` (string | object | array)
  - `questions` map: `{ id: { type, instructions, criteria? } }`
- Answer types: `noul` / `choice` / `score` under `answers`

Official docs: https://docs.typesafe.ai/api.md  
Official Python SDK (optional): `pip install typesafe-sdk` — https://docs.typesafe.ai/sdk/python.md

## Changes in 0.2.0 (2026-10-09)

- Retries (408/429/5xx/529 + network) with jittered backoff, Retry-After / retry-after-ms, ~30s budget.
- Pre-send validation matching typesafe-sdk 0.7.3; score criteria must be an ordered list (SDK 0.6.0 change).
- stderr summary: model, tokens, cost estimate ($0.042 / 1M input tokens; output free), noul |2p-1|.
- Size warnings vs 64k total / 32k state+longest-question limits (chars/4 estimate).
- `User-Agent: jev-cli/<version>`; `models --table` with lenient release_date parsing
  (live API returns full ISO timestamps, docs say YYYY-MM-DD).
- Balance: no API exists. `/openapi.json` lists only `/v1/systemone` and `/v1/models`;
  `/v1/usage|billing|balance|account|credits` return 404; response headers carry no
  credit info. Balance is only visible in the console (https://console.typesafe.ai).

## Secrets policy

- Keys live in env (`TYPESAFE_API_KEY` / `JEV_API_KEY`) or `~/.config/jev/api_key`
- `.gitignore` excludes `.env`, key files, and local config dirs
- Rotate any key that ever appears in chat transcripts
