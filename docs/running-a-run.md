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
node ui/server.js          # opens http://127.0.0.1:8765 (this computer only). Needs Node 18+ and nothing else.
```

The UI is JavaScript (`ui/server.js`, `ui/index.html`, `ui/app.js`). It asks `scripts/wf_api.py` for JSON and starts the Python workers.

- **One row per lane** (Planner, Dev, QE, Reviewer, Inspect & Adapt): its status (`idle`, `waiting`, `working`, `waiting for you`, `blocked`, `failed`), what it is doing on which ticket, a **live log** while it works on a ticket, and a **chat box**.
- **The chat box** does two things. When the lane has asked a ticket question, it shows the question and your text is the answer (the lane continues in the same session). Otherwise it is a chat with that lane outside any ticket: a long-lived session per lane (`scripts/wf_chat.py`); say what you want, and if the lane lacks something it asks. **Clear** starts a new conversation. Files a chat produces go to `tasks/chat-<lane>/` (git-ignored).
- **Tickets:** the list comes from `config/backlog.yaml` (fake for now; later the Jira board). **Start** creates the ticket run, fills its context and starts the workers, so the lanes process it one after the other. The table also lists each ticket's artifacts as chips with their status (draft, ready, approved, stale). Click a chip to read the artifact (a `changes/<repo>` chip shows that repo's files); when it belongs to a gate that waits for a person, the panel has **Approve** and **Reject** (with a reason) right there. **Open** shows gates (approve, reject), files, last logs, retry and accept.

It runs the same commands as below; use either. Set `WF_PYTHON` if your Python command is not `python3` or `python`.

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
