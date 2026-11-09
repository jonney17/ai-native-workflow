# LEGO AI workflow (skeleton)

PI planning gives a PRD per topic. Each PRD is split into tickets, and each ticket triggers **one run** of this flow.
Independent agents, one per role, share only **artifacts**. An agent acts when the artifacts it needs reach the right
status. Ticket status is derived from artifact and agent statuses, never typed.

This repo is only the **skeleton**: the hand-offs, statuses and gates. The agents are placeholders. Dev and QE (and the others)
later put their real task into the marked section of their agent file.

## Agents

> Naming: the roles are called **agents** everywhere: `config/agents.yaml`, the `agents:` key, `--agent`. Older setups that still use `config/lanes.yaml`, `lanes:`, `--lane` or `lane_*` events keep working.

The agents below are the **default configuration**, not code. Agents, what each one needs and produces, gates, per-repo fan-out and which agents run for each kind all live in `config/agents.yaml`. Edit them there or on the playbook's **Configuration** tab, which checks the config and creates a placeholder agent file for a new agent. `python scripts/wf_config.py` prints the agents per kind and any problem.

| Agent | Needs | Produces | Human gate |
|---|---|---|---|
| Architect (`planner`) | `ticket-context.md` | `design.md`, `tasks.md` | approve both together |
| QE | ticket criteria and approved tasks | `test-plan.md`, `tests/<repo>/` | |
| Dev (one per repo) | ticket criteria, approved design and tasks | `changes/<repo>/` | |
| Reviewer | all changes, test plan | `verify-report.md`, `review-report.md` | when `human_required: true` |

**Auditor** (not an agent): ONE agent for the whole team, with its own config (`config/auditor.yaml`: agent, model, which ticket statuses to audit, how often to check, auto start). It is off by default: start and stop it from the playbook's Auditor card or with `python scripts/wf_audit.py watch`. While it runs it audits each finished run into `audits/<ticket>/run-<n>.md` and never changes a ticket.

Merge and deploy are plain CI/CD after the review (the `ci` worker; today a placeholder). QE and Dev start together once the plan is approved.
Kinds (also config): `story` (default), `chore`, `spike` (Architect only, produces `findings.md`; approving it accepts the ticket).

## Run it

```
python -m pip install -r requirements.txt
python demo/demo.py auto            # whole story with stub agents, a fake human at the gates (about 15 seconds)
python scripts/smoke_test.py       # checks the flow, repo locks, the rework cap and old names with stub agents, in a temp copy (a few seconds)
```

With real Copilot agents (needs the `copilot` CLI), in a project folder with a ticket:

```
python scripts/new_ticket.py --ticket PROJ-1            # then fill tickets/PROJ-1/run-1/artifacts/ticket-context.md
python scripts/wf_run.py ready --ticket PROJ-1
python scripts/wf_run.py start --ticket PROJ-1 --verbose
python scripts/wf_status.py PROJ-1                      # where it stands and what a person must do next
python scripts/wf_audit.py watch                        # the Auditor (off by default): audits each finished ticket
```

The workers stop at human gates and print the command to continue. Or start the local Python playbook to view workflow status and run human actions:

```
.\playbook\start.ps1
```

Details: [docs/running-a-run.md](docs/running-a-run.md).
Design: [docs/workflow-design.md](docs/workflow-design.md). Demo: [demo/README.md](demo/README.md).

## Layout

```
.github/agents/  wf-<name>.agent.md: each agent's instructions, wf-auditor.agent.md (edit these)
skills/          _common.md (rules every agent reads) and templates/ per agent
config/          agents.yaml (agents, artifacts, gates, kinds: read through scripts/wf_config.py), auditor.yaml (the Auditor), backlog.yaml (fake ticket list), risk-rules.yaml, budgets.yaml, runner.yaml, repos.yaml
tickets/         tickets/<TICKET>/run-<N>/ : manifest.yaml, events.log, decisions.md, artifacts/
audits/          audits/<TICKET>/run-<N>.md: the Auditor's reports (and audits/events.log)
playbook/        server.py, index.html, config.html (Configure agents), start.ps1: the local workflow UI (Python)
scripts/         wf_acp.py (long-lived agent session), acp_probe.py (experiment), new_ticket.py, wf_status.py (read-only view), wf_run.py (workers + human commands), wf_audit.py (the Auditor), wf_config.py (reads and checks agents.yaml), wf_api.py (JSON workflow interface), wf_chat.py (chat with an agent), wf_state.py, wf_launch.py
demo/            demo.py and stub_agent.py
```

## Removed to keep it simple

Overlays, the ledger and the learning loop, OTEL telemetry and its checker, the pilot-spike notes, the slide animation and the
scripted demo story with fake repos. They are in git history (last commit before the cleanup: `628f345`) if the team wants them back.
