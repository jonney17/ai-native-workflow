# Running a run

One trigger starts one worker per agent. Each worker is a small loop:

1. derive the state from the files (`wf_status.py`),
2. when its agent is ready and nobody holds it, take a lease,
3. launch one agent session (`wf_launch.py`),
4. mark the artifacts the session wrote as `ready`, with the hashes of their inputs,
5. apply the problem events the agent wrote, once each,
6. look again.

Workers share only the run folder. No central controller.

## Start

```
python scripts/new_ticket.py --ticket PROJ-1 --kind story      # then fill artifacts/ticket-context.md
python scripts/wf_run.py ready --ticket PROJ-1                 # refused if the ticket has no criteria or repo; then the Architect may start
python scripts/wf_run.py start --ticket PROJ-1 --verbose       # the one trigger; Ctrl+C stops all workers
```

It ends when the ticket is Done or Escalated. Agent output is saved in `logs/` of the run folder.

## The playbook: workflow status and actions

```
.\playbook\start.ps1       # opens http://127.0.0.1:8765 on this computer only
```

The playbook is served by `playbook/server.py` and uses the same Python workflow scripts described below. It shows tickets, derived status, agent and artifact readiness, and the next available human actions. Buttons run the corresponding workflow commands and display their output. It binds to localhost; stop it with `Ctrl+C`.

The playbook has three tabs:

- **Jira board**: one card per ticket assigned to you (`assignee` in `config/backlog.yaml`, the fake board for now, matched against `WF_USER` or your OS user name; **All** shows every ticket), in columns Not started, Running, Done and Archived. **Start working** creates the ticket's run, writes `ticket-context.md` from the ticket, marks it ready, starts the agent workers and opens the Ticket runs tab. A Done ticket can be archived (**Archive**) and an archived one restored (**Restore**).
- **Ticket runs**: one tab per running ticket (Done ones leave this list), with the Auditor card on top. **Start / resume workflow** and **Stop** sit at the top right. The canvas shows the agents and a card for every artifact; drag anything to arrange it (remembered in the browser, **Reset layout** undoes it). Hover or click a node to see its dotted connectors: input (artifact to agent, blue) and output (agent to artifact, green), amber while an agent waits. Hover an agent's status to read its latest log, or the **Live log** icon for the whole run (it follows the newest line until you scroll up). **Needs your decision** appears only when a person must approve, answer, confirm a deploy or accept.
- **Configuration**: agents and their artifacts, the agents and done condition of each kind, and the Auditor's settings (the Configure page, also at `/config`).

The **Configuration** tab (also `/config`) edits `config/agents.yaml`. Each agent card has its name, what it **needs**, what it **produces**, its **instructions** file, and two switches: *a person approves its output* (a gate) and *one per repo*. **More** holds the rest (status name, model, gate options). On the right, pick a kind, tick its agents and set **Done when** (deploy, a person accepts, extra artifacts). **Start blank** clears the agents to build a team from scratch. The page checks everything as you edit and saves only a valid setup; it keeps the previous file as `config/agents.yaml.bak` (YAML comments other than the header are not kept) and creates a placeholder agent file for an agent whose instructions file does not exist yet.

The **Auditor** card at the top of the playbook starts and stops the team's single Auditor (`scripts/wf_audit.py watch`); it is off by default unless `auto_start: true` in `config/auditor.yaml` (also on the Configure page). The card shows each finished ticket's audit state, **View audit** and **Audit again**. Without the playbook, run `python scripts/wf_audit.py watch` yourself.

Automatic polling updates the agent cards and artifact nodes in place, including agent and repo statuses, while preserving the canvas layout and any text entered in human-action controls.

Rejecting the Architect's plan replaces the `## Plan rejection feedback` section in `ticket-context.md`, marks the updated context ready, marks the plan artifacts rejected, and restarts the workflow if workers are not already running. The Planner uses the updated context to regenerate the plan; active workers pick up the rework automatically.

Install the repository dependencies first with `python -m pip install -r requirements.txt`. You can also start the server directly with `python playbook/server.py [port]`.

## What people do

| When | Command |
|---|---|
| Plan (or spike findings) is ready | `wf_run.py approve --ticket T --gate planner`, or `reject --gate planner --reason "..."` |
| Review needs a human | `wf_run.py approve --ticket T --gate reviewer` |
| An agent asked a question | `wf_run.py answer --ticket T --agent L [--repo R] --text "..."` |
| An agent gave up after its attempts | `wf_run.py retry --ticket T --agent L [--repo R]`, then `start` again |
| Deployed outside the workers | `wf_run.py deployed --ticket T --repo R` |
| Acceptance (policy TBD) | `wf_run.py accept --ticket T` |

`start` prints the exact command when it waits for a person. `wf_run.py command --ticket T --agent L` prints the agent command a worker would run.

## Agents

Each agent is a Copilot custom agent: `.github/agents/wf-<name>.agent.md` holds the role, the allowed `tools` and the instructions. There is no build step; edit the file. Rules shared by all agents are in `skills/_common.md`, artifact templates in `skills/<name>/templates/`.

**Sessions (`mode` in `config/runner.yaml`).**
- `acp` (default): an agent runs as one long-lived `copilot --acp --stdio` session (ACP, public preview). When the agent needs something it stops and asks; the agent shows as `blocked` / `waiting for you`; your answer (UI or `wf_run.py answer`) is sent to the SAME session, so the agent continues with what it already read. The session stays open only while the worker runs; if the worker is restarted, the agent starts a fresh session and gets your answer in its task message. A question does not count as a failed attempt.
- `print`: one-shot `copilot -p ... --no-ask-user` sessions. The agent asks and stops; your answer goes into a new session's task message.

If an agent stops without finishing its artifacts and without writing an event, the orchestrator treats its last message as its question.

In `acp` mode permission requests are approved by the worker, except anything containing `git push`. The Free plan cannot pin a model. For tests set `WF_ACP_CMD` (a JSON list) to a fake ACP server (the demo uses `demo/stub_acp.py`) or `WF_AGENT_CMD` for `print` mode.

Today the agents are placeholders: they write short artifacts and no code. They have no shell (`tools: read, search, edit`), so nothing asks for permissions. When a team member adds a real task that needs the shell, add `execute` to `tools` in that agent file.

## Rules the workers follow

- One session per agent instance (per repo for Dev), through a lease that expires (`config/budgets.yaml`).
- Attempts: counted from `agent_finished` events; after `max_attempts` the agent fails and the ticket is Escalated.
- Outcomes: `ok`, `incomplete` (nothing usable, retried), `crashed` (non-zero exit, retried), `blocked` (a question).
- Only workers and the commands above write `manifest.yaml`. Agents never do.
