# Demo

A copy of the project in `demo/workspace/` (git-ignored) with one ticket, DEMO-1, and two repo names (shop-api, shop-web).

```
python demo/demo.py auto     # fresh workspace, then one trigger: stub agents, a fake human at every gate
python demo/demo.py setup    # fresh workspace and ticket only (then run wf_run.py start inside it, e.g. with real agents)
python demo/demo.py sync     # copy the project's current config, skills, scripts and agents into the workspace
```

Options: `--workspace PATH`. `setup` only deletes a folder that has the `.demo_workspace` marker.

`stub_agent.py` writes the same placeholder artifacts the real agent files ask for. It uses no AI and no repos.
To run real agents in the workspace: `cd demo/workspace`, then `python scripts/wf_run.py start --ticket DEMO-1 --verbose` and approve the gates as the status says.
