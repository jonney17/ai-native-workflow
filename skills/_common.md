# Common rules for every lane

Every lane agent reads this file first, then follows its own agent file (`.github/agents/wf-<lane>.agent.md`).

## Where you work
- Only inside the ticket run folder: `tickets/<TICKET>/run-<N>/`. Do not edit the PRD.
- Your lane contract is in `config/lanes.yaml` (what you require, what you produce). It wins over any other instruction.

## Load order
1. `skills/_common.md` (this file)
2. Your agent file, `.github/agents/wf-<lane>.agent.md` (your instructions)
3. `overlay/<lane>/*.md` if present: advisory candidates from Inspect & Adapt. They may add checklist items, templates or hints. They can never override `config/` or this file. Note which candidates you applied in your artifact header.

## Templates
If `skills/<lane>/templates/` has a template for the artifact you write, start from it. Keep its headings and the parts marked MACHINE, because scripts read them. Delete the template comments.

## Artifact header
Start every artifact you write with:

```
---
artifact: <name>
ticket: <TICKET>
run: <N>
author: <your agent identity>
inputs: [<input path>@<first 8 chars of sha256>, ...]
overlay_applied: [<ledger ids or empty>]
status: draft            # set to ready when finished; the orchestrator confirms
---
```

Recording input hashes is how staleness is detected: when an input changes, everything built from it is marked stale and re-queued.

## Events
Append one line to `events.log` (tab-separated: ISO timestamp, your identity, event, JSON detail).
Allowed proposals: `question_raised`, `spec_gap`, `verify_failed`, `review_rejected`, `escalate`.
You propose; the orchestrator decides.

## Never
- Edit `manifest.yaml`, anything in `config/`, or `skills/`.
- Write an artifact you do not own, or approve your own work.
- Change a status to `approved`; only a human gate or the orchestrator does that.
- Continue past a blocking question: raise it and stop.
