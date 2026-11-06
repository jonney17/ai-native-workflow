# Workflow playbook

The local playbook displays ticket, lane, and artifact readiness and provides controls for the existing workflow commands.

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

The **Work items** panel opens automatically when active work items are present. You can collapse it into a narrow left rail on desktop to give the workflow more horizontal space; the playbook remembers your choice. On smaller screens the panel is a compact header above the workflow. **Remove** archives a work item, including its runs and artifacts, under `tickets/_archive/`. Restore it from the panel's **Archived** list; work items with active commands cannot be archived.

Select a backlog item in the **Work items** panel, then use the top-right **Start workflow** button to create its run, generate `ticket-context.md` from the backlog title, description, criteria, and repos, mark the context ready, and start or resume all workers. Existing hand-edited context is preserved. For non-backlog tickets, the button starts or resumes the selected ticket when its context is ready. The button reflects the selected workflow's running, complete, and archived state; backlog status stays synchronized while the playbook is open. Workers proceed through available lanes and wait at human gates or missing prerequisites.

The **Refresh** button and automatic polling update each workflow-lane card and artifact-flow node in place, including lane and repo statuses, while preserving the diagram layout and any text entered in human-action controls.

## Human decisions

- **Reject plan** replaces the `## Plan rejection feedback` section in `ticket-context.md`, marks the updated context ready, rejects the plan artifacts, and restarts the workflow when workers are not already running. The Planner receives the updated context and regenerates the plan; active workers pick up the rework automatically.
- **Approve review** appears only while the reviewer gate is awaiting approval, and invokes `wf_run.py approve --ticket T --gate reviewer`.
- **Confirm deployment complete** appears during deployment. For ordered releases, the playbook offers only the next repository in the release sequence. Confirm only after that repository has actually been deployed; the action records it with `wf_run.py deployed --ticket T --repo R`.
- **Accept ticket** appears only after all required deployments are recorded. It invokes `wf_run.py accept --ticket T`.

The playbook validates that an action is currently allowed before launching it. Workflow state and gate rules remain owned by the scripts under `scripts/`; the playbook is their UI, not a separate workflow implementation.
