---
name: typesafe-ai
license: MIT
description: >
  Build with TypeSafe System One (Jev): typed judgments (noul / choice / score)
  over application state. Use for routing, ranking, extraction, verification,
  and similar decisions. Prefer live docs over this summary.
---

# TypeSafe / Jev (vendored summary)

Full upstream skill (keep in sync when it changes):

- https://github.com/typesafe-ai/skills/blob/main/skills/typesafe-ai/SKILL.md
- Raw: https://raw.githubusercontent.com/typesafe-ai/skills/main/skills/typesafe-ai/SKILL.md

Live docs index: https://docs.typesafe.ai/llms.txt

## HTTP API (this repo’s CLI)

- `POST https://api.typesafe.ai/v1/systemone`
- `GET https://api.typesafe.ai/v1/models`
- Auth: `Authorization: Bearer <TYPESAFE_API_KEY>`
- Body: `{ "model", "state", "questions" }`
- Question types: `noul`, `choice`, `score`

## How to use from this CLI

```bash
export TYPESAFE_API_KEY=…   # never commit
jev models
jev ask --request examples/ticket.request.json
```

Optional official SDK: `pip install typesafe-sdk` — see https://docs.typesafe.ai/sdk/python.md

## Design rules (short)

1. Keep code in control; Jev returns calibrated judgments, not free text.
2. Put context in `state`; put the decision in `instructions` / `criteria`.
3. Prefer one narrow question per id; ask independent questions in one call.
4. Use probabilities / confidence in application code (thresholds, escalate to human).
5. Read the live docs before inventing request shapes or SDK details.
