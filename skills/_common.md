# Common rules for every lane

Every lane agent reads this file first, then follows its own agent file (`.github/agents/wf-<lane>.agent.md`).

## Where you work
- Only inside the ticket run folder: `tickets/<TICKET>/run-<N>/`. Do not edit the PRD or any repo.
- Your lane contract is in `config/lanes.yaml` (what you require, what you produce). It wins over any other instruction.

## Load order
1. `skills/_common.md` (this file)
2. Your agent file, `.github/agents/wf-<lane>.agent.md` (your instructions)

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
inputs: [<path of each input you read>, ...]       # paths only: the orchestrator records the hashes in the manifest
status: draft            # set to ready when finished; the orchestrator confirms
---
```

Staleness is detected from input hashes. You do not compute them (most lanes have no shell): the orchestrator records them in the manifest when it marks your artifact ready. When an input changes, everything built from it is marked stale and re-queued.

## Events
Append one line to `events.log` (tab-separated: ISO timestamp in UTC, your identity, event, JSON detail). Your identity is your lane name, for example `planner` or `dev-shop-web`, never a person's name.
**A proposal reports a problem. If nothing is wrong, write no event at all.** Never log progress, completion or "no gaps found": the launcher and the orchestrator record those from your artifacts. The orchestrator acts on every proposal it accepts, so a needless one sends work back.

| Proposal | Who may write it | Meaning | Effect |
|---|---|---|---|
| `question_raised` | any lane | You are blocked and need a human answer | your lane waits until a human answers |
| `spec_gap` | qe, dev, reviewer | The approved spec is missing or contradicts something needed | spec.md goes back to the planner |
| `verify_failed` | reviewer | A repo's changes fail verification (detail: `repo`, `acs`) | only that repo's Dev instance redoes its work |
| `review_rejected` | reviewer | The review finds the change unacceptable (detail: `reason`, optional `repo`) | the review and that repo's changes go back |
| `escalate` | any lane | You cannot continue and a person must decide | the ticket is escalated |

The planner never proposes `spec_gap` for its own spec: if it cannot finish, it writes `question_raised`. You propose; the orchestrator decides.

## Never
- Edit `manifest.yaml`, anything in `config/`, or `skills/`.
- Write an artifact you do not own, or approve your own work.
- Change a status to `approved`; only a human gate or the orchestrator does that.
- Continue past a blocking question: raise it and stop.
