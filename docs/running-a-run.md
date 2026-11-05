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

## The UI: the team as lanes

```
python scripts/wf_ui.py          # opens http://127.0.0.1:8765 (this computer only)
```

- **Team board:** one card per lane (Planner, Dev, QE, Reviewer, Inspect & Adapt) with its status: `idle`, `waiting`, `working`, `waiting for you`, `blocked`, `failed`, and what it is doing on which ticket.
- **Tickets:** the list comes from `config/backlog.yaml` (fake for now; later the Jira board). **Start** creates the ticket run, fills the ticket context and starts the workers, so the lanes process it one after the other. Click a ticket to see its lanes, read artifacts and logs, answer questions, approve or reject at the gates, retry a lane that gave up, and accept the ticket.
- **Ask a lane:** the **Ask** button on any lane gives it one task outside a ticket. The form says what the lane needs from you (`ask_for` in `config/lanes.yaml`). If something is missing the lane writes a question; it shows on the page, you answer, and the lane runs again. The result appears on the page. Requests live in `tasks/` (git-ignored).

It runs the same commands as below; use either.

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

**Sessions (`mode` in `config/runner.yaml`).**
- `acp` (default): a lane runs as one long-lived `copilot --acp --stdio` session (ACP, public preview). When the lane needs something it stops and asks; the lane shows as `blocked` / `waiting for you`; your answer (UI or `wf_run.py answer`) is sent to the SAME session, so the lane continues with what it already read. The session stays open only while the worker runs; if the worker is restarted, the lane starts a fresh session and gets your answer in its task message. A question does not count as a failed attempt.
- `print`: one-shot `copilot -p ... --no-ask-user` sessions. The lane asks and stops; your answer goes into a new session's task message.

If an agent stops without finishing its artifacts and without writing an event, the orchestrator treats its last message as its question.

In `acp` mode permission requests are approved by the worker, except anything containing `git push`. The Free plan cannot pin a model. For tests set `WF_ACP_CMD` (a JSON list) to a fake ACP server (the demo uses `demo/stub_acp.py`) or `WF_AGENT_CMD` for `print` mode.

Today the agents are placeholders: they write short artifacts and no code. They have no shell (`tools: read, search, edit`), so nothing asks for permissions. When a team member adds a real task that needs the shell, add `execute` to `tools` in that agent file.

## Rules the workers follow

- One session per lane instance (per repo for Dev), through a lease that expires (`config/budgets.yaml`).
- Attempts: counted from `lane_finished` events; after `max_attempts` the lane fails and the ticket is Escalated.
- Outcomes: `ok`, `incomplete` (nothing usable, retried), `crashed` (non-zero exit, retried), `blocked` (a question).
- Only workers and the commands above write `manifest.yaml`. Agents never do.
