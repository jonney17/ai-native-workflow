# Workflow playbook

The local playbook displays ticket, agent, and artifact readiness and provides controls for the existing workflow commands.

## Start the playbook

Install the Python dependencies once from the repository root:

```powershell
python -m pip install -r requirements.txt
```

The playbook needs PyYAML to read workflow manifests. If you use Python 3.15 on Windows and the install fails because a C++ compiler is missing, install PyYAML without its optional C extension:

```powershell
$env:PYYAML_FORCE_LIBYAML = "0"
python -m pip install --no-binary=PyYAML -r requirements.txt
```

Then start it from the repository root:

```powershell
.\playbook\start.ps1
```

The script starts the local server on port `8765`. Open [http://127.0.0.1:8765](http://127.0.0.1:8765) in your browser. The server binds to localhost only; press `Ctrl+C` in the terminal to stop it.

To use a different port, pass it to the script and open the matching URL:

```powershell
.\playbook\start.ps1 -Port 8766
```

The playbook has three tabs:

- **Jira board**: one card per ticket assigned to you (`assignee` in `config/backlog.yaml`, the fake board for now, matched against `WF_USER` or your OS user name; **All** shows every ticket), in columns Not started, Running, Done and Archived. **Start working** creates the ticket's run, writes `ticket-context.md` from the ticket, marks it ready, starts the agent workers and opens the Ticket runs tab. A Done ticket can be archived (**Archive**) and an archived one restored (**Restore**).
- **Ticket runs**: one tab per running ticket (Done ones leave this list), with the Auditor card on top. **Start / resume workflow** and **Stop** sit at the top right. The canvas shows the agents and a card for every artifact; drag anything to arrange it (remembered in the browser, **Reset layout** undoes it). Hover or click a node to see its dotted connectors: input (artifact to agent, blue) and output (agent to artifact, green), amber while an agent waits. Hover an agent's status to read its latest log, or the **Live log** icon for the whole run (it follows the newest line until you scroll up). **Needs your decision** appears only when a person must approve, answer, confirm a deploy or accept.
- **Configuration**: agents and their artifacts, the agents and done condition of each kind, and the Auditor's settings (the Configure page, also at `/config`).

Automatic polling updates the agent cards and artifact nodes in place, including agent and repo statuses, while preserving the canvas layout and any text entered in human-action controls.

## Configuration tab

The **Configuration** tab (also `/config`) shows agents with what they need and produce, their instructions file, whether a person approves
their output and whether they run once per repo; per kind, which agents run and when a ticket is done. **Start blank** clears the
agents. The page checks the setup as you edit and saves only a valid one; the agent cards and the artifact flow follow it.

## Auditor

The Auditor card shows the team's single Auditor (`scripts/wf_audit.py watch`). It is off by default: press **Start** to run it
and **Stop** to end it, or tick *Start it automatically* under Auditor on the Configure page (`auto_start` in
`config/auditor.yaml`). While it runs it audits each finished ticket into `audits/<ticket>/run-<n>.md`; the card shows
**View audit** and **Audit again** per ticket.

## Human decisions

- **Reject plan** replaces the `## Plan rejection feedback` section in `ticket-context.md`, marks the updated context ready, rejects the plan artifacts, and restarts the workflow when workers are not already running. The Planner receives the updated context and regenerates the plan; active workers pick up the rework automatically.
- **Approve review** appears only while the reviewer gate is awaiting approval, and invokes `wf_run.py approve --ticket T --gate reviewer`.
- **Confirm deployment complete** appears during deployment. For ordered releases, the playbook offers only the next repository in the release sequence. Confirm only after that repository has actually been deployed; the action records it with `wf_run.py deployed --ticket T --repo R`.
- **Accept ticket** appears only after all required deployments are recorded. It invokes `wf_run.py accept --ticket T`.

The playbook validates that an action is currently allowed before launching it. Workflow state and gate rules remain owned by the scripts under `scripts/`; the playbook is their UI, not a separate workflow implementation.
