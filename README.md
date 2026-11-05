# LEGO AI workflow (skeleton)

PI planning gives a PRD per topic. Each PRD is split into tickets, and each ticket triggers **one run** of this flow.
Independent agents, one per role (lane), share only **artifacts**. A lane acts when the artifacts it needs reach the right
status. Ticket status is derived from artifact and lane statuses, never typed.

This repo is only the **skeleton**: the hand-offs, statuses and gates. The agents are placeholders. Dev and QE (and the others)
later put their real task into the marked section of their agent file.

## Lanes

| Lane | Needs | Produces | Human gate |
|---|---|---|---|
| Planner | `ticket-context.md` | `spec.md`, `design.md`, `tasks.md` | approve the three together |
| QE | approved spec and tasks | `test-plan.md`, `tests/<repo>/` | |
| Dev (one per repo) | approved spec, design, tasks | `changes/<repo>/` | |
| Reviewer | all changes, test plan | `verify-report.md`, `review-report.md` | when `human_required: true` |
| Inspect & Adapt | accepted ticket | `inspection-report.md` | |

Merge and deploy are plain CI/CD after the review (the `ci` worker; today a placeholder). QE and Dev start together once the plan is approved.
Kinds: `story` (default), `chore`, `spike` (Planner only, produces `findings.md`).

## Run it

```
python -m pip install -r requirements.txt
python demo/demo.py auto            # whole story with stub agents, a fake human at the gates (about 15 seconds)
```

With real Copilot agents (needs the `copilot` CLI), in a project folder with a ticket:

```
python scripts/new_ticket.py --ticket PROJ-1            # then fill tickets/PROJ-1/run-1/artifacts/ticket-context.md
python scripts/wf_run.py ready --ticket PROJ-1
python scripts/wf_run.py start --ticket PROJ-1 --verbose
python scripts/wf_status.py PROJ-1                      # where it stands and what a person must do next
```

The workers stop at human gates and print the command to continue. Or use the web UI, which shows the team as lanes, a ticket list and one-off requests: `python scripts/wf_ui.py`. Details: [docs/running-a-run.md](docs/running-a-run.md).
Design: [docs/workflow-design.md](docs/workflow-design.md). Demo: [demo/README.md](demo/README.md).

## Layout

```
.github/agents/  wf-<lane>.agent.md: each lane's instructions (edit these)
skills/          _common.md (rules every lane reads) and templates/ per lane
config/          lanes.yaml (who needs/produces what), backlog.yaml (fake ticket list), risk-rules.yaml, budgets.yaml, runner.yaml, repos.yaml
tickets/         tickets/<TICKET>/run-<N>/ : manifest.yaml, events.log, decisions.md, artifacts/
scripts/         new_ticket.py, wf_status.py (read-only view), wf_run.py (workers + human commands), wf_ui.py (local web UI), wf_task.py (one-off lane requests), wf_state.py, wf_launch.py
demo/            demo.py and stub_agent.py
```

## Removed to keep it simple

Overlays, the ledger and the learning loop, OTEL telemetry and its checker, the pilot-spike notes, the slide animation and the
scripted demo story with fake repos. They are in git history (last commit before the cleanup: `628f345`) if the team wants them back.
