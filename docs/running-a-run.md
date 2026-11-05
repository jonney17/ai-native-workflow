# Running a run

One trigger starts one worker per lane. Each worker is a small loop:

1. derive the state from the files (`wf_status.py`),
2. when its lane is ready and nobody holds it, take a lease,
3. launch one agent session (`wf_launch.py`),
4. mark the artifacts the session wrote as `ready`, with the hashes of their inputs,
5. apply the problem events the agent wrote, once each,
6. look again.

Workers share only the run folder. No central controller.

## Start

```
python scripts/new_ticket.py --ticket PROJ-1 --kind story      # then fill artifacts/ticket-context.md
python scripts/wf_run.py ready --ticket PROJ-1                 # the Planner may start
python scripts/wf_run.py start --ticket PROJ-1 --verbose       # the one trigger; Ctrl+C stops all workers
```

It ends when the ticket is Done or Escalated. Agent output is saved in `logs/` of the run folder.

## A simple UI

```
python scripts/wf_ui.py          # opens http://127.0.0.1:8765 (this computer only, no extra packages)
```

Pick a ticket, press **Start workers**, and watch each lane. For every lane you can read its last agent log and its
artifacts, answer its question (the answer goes into the lane's next task message), retry it after it gave up, and
approve or reject at the gates. It runs the same commands as below; use either.

## What people do

| When | Command |
|---|---|
| Plan (or spike findings) is ready | `wf_run.py approve --ticket T --gate planner`, or `reject --gate planner --reason "..."` |
| Review needs a human | `wf_run.py approve --ticket T --gate reviewer` |
| An agent asked a question | `wf_run.py answer --ticket T --lane L [--repo R] --text "..."` |
| A lane gave up after its attempts | `wf_run.py retry --ticket T --lane L [--repo R]`, then `start` again |
| Deployed outside the workers | `wf_run.py deployed --ticket T --repo R` |
| Acceptance (policy TBD) | `wf_run.py accept --ticket T` |

`start` prints the exact command when it waits for a person. `wf_run.py command --ticket T --lane L` prints the agent command a worker would run.

## Agents

Each lane is a Copilot custom agent: `.github/agents/wf-<lane>.agent.md` holds the role, the allowed `tools` and the instructions. There is no build step; edit the file. Rules shared by all lanes are in `skills/_common.md`, artifact templates in `skills/<lane>/templates/`.

The command is in `config/runner.yaml` (`copilot --agent wf-<lane> -p ... --allow-all-tools --no-ask-user`). The Free plan cannot pin a model. To use another command set `WF_AGENT_CMD` (a JSON list); the demo does this with a stub.

Today the agents are placeholders: they write short artifacts and no code. They have no shell (`tools: read, search, edit`), so nothing asks for permissions. When a team member adds a real task that needs the shell or other folders, add `execute` to `tools` in that agent file and check what the CLI still asks for.

## Rules the workers follow

- One session per lane instance (per repo for Dev), through a lease that expires (`config/budgets.yaml`).
- Attempts: counted from `lane_finished` events; after `max_attempts` the lane fails and the ticket is Escalated.
- Outcomes: `ok`, `incomplete` (nothing usable, retried), `crashed` (non-zero exit, retried), `blocked` (a question).
- Only workers and the commands above write `manifest.yaml`. Agents never do.
