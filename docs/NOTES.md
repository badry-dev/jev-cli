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

## Secrets policy

- Keys live in env (`TYPESAFE_API_KEY` / `JEV_API_KEY`) or `~/.config/jev/api_key`
- `.gitignore` excludes `.env`, key files, and local config dirs
- Rotate any key that ever appears in chat transcripts
