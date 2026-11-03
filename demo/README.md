# Demo kit

A runnable demo of the workflow with **fake repos and scripted agents**. No AI, no Jira, no real code is involved, so it is safe to run anywhere and always gives the same result.

## What is real and what is fake

| Real | Fake |
|------|------|
| `wf_launch.py`, `otel_check.py`, `wf_status.py` and `new_ticket.py` (copied into the workspace) | The agents: `stub_agent.py` writes scripted text, it does not think |
| Lane contracts, risk rules, staleness, derived ticket status | The orchestrator and the human: `demo.py` plays both |
| Git branches, commits and merges in two tiny fake repos | The telemetry numbers (marked `demo.synthetic`) |
| Real test runs, including one real failure and its rework | The CI/CD deploy (a real `git merge` plus a test run) |

## Run it

```
python -m pip install -r requirements.txt        # once, for PyYAML
python demo/demo.py setup                         # builds demo/workspace (git-ignored)
python demo/demo.py next                          # runs one step, then shows the ticket status
python demo/demo.py status                        # shows the status again, any time
python demo/demo.py reset                         # wipes the workspace and starts over
```

Other commands: `list` (the 13 steps), `run` (all remaining steps, pausing for Enter), `stale on|off` (the stale-cascade detour). Add `--fast` to remove the delays and pauses.

For a live presentation use `next` one step at a time and follow [WALKTHROUGH.md](WALKTHROUGH.md).

## Safety

- Everything happens in `demo/workspace/`. Your real `tickets/`, `ledger/`, `overlay/` and `config/` are only read, never written.
- `setup` and `reset` only delete a folder that contains the marker file `.demo_workspace`. A folder without it is refused.
- Use `--workspace PATH` to put the workspace somewhere else (for example `C:\temp\wf-demo`).

## Where things are

```
demo/
  demo.py            the driver (orchestrator and human)
  stub_agent.py      the scripted agent started by wf_launch.py
  content/scripted.py   every text the agents write, edit it to change the story
  seed/              the two fake repos: shop-api and shop-web
  workspace/         created by setup (git-ignored): tickets, ledger, overlay, otel-dump, repos
```

## Make it your own

Edit `content/scripted.py` to change the ticket, the spec or the code the "agents" write. To demo a real lane, run one real agent session in the workspace instead of the stub, for example:

```
cd demo/workspace
python scripts/wf_launch.py --ticket DEMO-2 --lane planner -- copilot
```

Keep in mind that the workspace skills are copies. Changes to `skills/` in the project do not reach an existing workspace until you run `reset`.
