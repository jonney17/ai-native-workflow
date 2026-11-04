# Running a run: one trigger, every lane works on its own

There is no central controller and no fixed pipeline. A run is a set of **lane workers**. Each one is a small loop that:

1. derives the state from the files (`wf_status.py` logic),
2. when its lane is ready and nobody holds it, takes a **lease**,
3. launches one agent session through `wf_launch.py` (so telemetry is tagged),
4. runs `otel_check.py`, marks the artifacts that session wrote as `ready` with their input hashes,
5. applies the events agents proposed (`verify_failed`, `spec_gap`, `question_raised`, ...), once each,
6. looks again.

Workers share nothing but the run folder. You can run them all with one command or one per terminal.

## Start a run

```
python scripts/new_ticket.py --ticket PROJ-1 --kind story      # then fill artifacts/ticket-context.md
python scripts/wf_run.py ready --ticket PROJ-1                 # intake done: the Planner may start
python scripts/wf_run.py start --ticket PROJ-1                 # the one trigger
```

`start` launches one worker per lane (plus `ci`, the CI/CD stand-in, for stories and chores) and prints what they do. It stops when the ticket is Done or Escalated. Add `--verbose` to see the agents' output; it is always saved to `logs/` in the run folder.

## What humans do

The workers stop at a human gate and `start` prints the exact command. Nothing is edited by hand any more.

| When | Command |
|---|---|
| Plan (or spike findings) is ready | `wf_run.py approve --ticket T --gate planner` or `reject --gate planner --reason "..."` |
| Review is risk-flagged | `wf_run.py approve --ticket T --gate reviewer` |
| An agent asked a question (`question_raised`) | `wf_run.py answer --ticket T --lane planner --text "..."` |
| Deployed outside the workers (no `deploy.command`) | `wf_run.py deployed --ticket T --repo R` |
| Acceptance (policy still TBD) | `wf_run.py accept --ticket T` |

## Rules the workers follow

- **Lease:** one session per lane instance (per repo for Dev), created atomically. A crashed session's lease expires (`config/budgets.yaml`, `lease_minutes`).
- **Attempts:** each launch gets the next attempt number, counted from `lane_finished` events. After `max_attempts` the lane is marked failed and the ticket is Escalated.
- **Outcome:** `ok` (artifacts written, or a proposal applied), `incomplete` (nothing usable), `crashed` (non-zero exit), `blocked` (a question). Incomplete and crashed sessions are retried.
- **Rework:** `verify_failed` rejects `changes/<repo>` only; that Dev instance starts again and the verify report goes stale, which sends the Reviewer back.
- **Release order:** the `ci` worker deploys in the order from `tasks.md` and stops on the first failure.
- **Manifest writes** are atomic and locked; only workers and the commands above write it. Agents never do.

## Agents

`config/runner.yaml` holds the command. The default is Copilot CLI in prompt mode (`copilot -p "<prompt>" --allow-all-tools --no-ask-user ...`) with the lane prompt built from the template there. **This command has not been run by the workers yet**; try it on a spike first. Per-lane flags (`--deny-tool`, `--max-ai-credits`, a custom `--agent`) go under `agent.by_lane`. To use any other command, set `WF_AGENT_CMD` (a JSON list).

## Try it without AI

```
python demo/demo.py auto                    # the whole DEMO-1 story in a fresh workspace, humans simulated
python demo/demo.py auto --gates-by-hand    # same, but you approve at the gates
```

`--auto-approve` on `start` plays the human at every gate. It is for testing the flow only.
