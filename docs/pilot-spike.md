# Pilot: run one real spike

Goal: see the workflow work on a real question, and find out whether real Copilot telemetry has what we assumed. A spike is the safest first ticket: only the Planner lane runs, and there is no code, test or deploy.

Commands are for Windows PowerShell from the project root. Use `python3` or `python`, whichever you use for the other scripts.

## Before you start (once)

1. `python -m pip install -r requirements.txt`
2. Check the Copilot CLI starts and see how it takes a starting prompt: `copilot --help`. The launcher passes whatever you put after `--` unchanged.
3. If the spike looks at your repos, register them. Copy `config/repos.local.example.yaml` to `config/repos.local.yaml` and set the paths, and add the repo names to `config/repos.yaml`. The Planner finds the paths in the `WF_REPO_PATHS` environment variable. A spike about documents or the web needs no repos.

## Pick the spike

A real question with a decision behind it, small enough for a few hours of agent work. Write down in one line what you would have done without the workflow, so you can compare at the end.

## Steps

1. **Create the ticket.**
   `python3 .\scripts\new_ticket.py --ticket SPIKE-1 --kind spike --origin experiment`
2. **Fill in the context.** Edit `tickets\SPIKE-1\run-1\artifacts\ticket-context.md`: Question, Decision it informs, Time box, Starting points.
3. **Mark it ready.** In `tickets\SPIKE-1\run-1\manifest.yaml` set `ticket-context.md` to `{ owner: system, status: ready }`. (Until the orchestrator is built, the manifest is edited by hand.)
4. **Check the status.** `python3 .\scripts\wf_status.py SPIKE-1` should say Investigating, with the Planner ready and a launch command under Next.
5. **Launch the Planner** through the launcher, so the telemetry is tagged:
   `python3 .\scripts\wf_launch.py --ticket SPIKE-1 --run 1 --lane planner -- copilot <your usual arguments>`
   Give the agent this as its first instruction (adjust the ticket id):

   > You are the Planner lane for ticket SPIKE-1, run 1, kind spike. Read `skills/_common.md`, then `.github/agents/wf-planner.agent.md` (the spike section), then `tickets/SPIKE-1/run-1/artifacts/ticket-context.md`. Investigate the question. The repos you may read are in the environment variable WF_REPO_PATHS. Write only `tickets/SPIKE-1/run-1/artifacts/findings.md`, in the format the instructions describe. Do not edit `manifest.yaml`, anything in `config/` or `skills/`. If you are blocked, append a `question_raised` line to `events.log` and stop. Stop at the time box.

6. **Check the telemetry** as soon as the session ends:
   `python3 .\scripts\otel_check.py --ticket SPIKE-1 --run 1 --lane planner`
   It writes `tickets\SPIKE-1\run-1\metrics\planner-a1.json`. See "What to look at" below.
7. **Read the findings.** If they are usable, set `findings.md` in the manifest to `{ owner: planner, status: ready }`. To make staleness tracking work, also record its input: `inputs: { ticket-context.md: "<hash>" }` where the hash comes from `python3 .\scripts\wf_status.py --hash .\tickets\SPIKE-1\run-1\artifacts\ticket-context.md` (quote it).
8. **Decide.** `wf_status` now says Awaiting findings review. To accept, set `findings.md` to `status: approved` with `approved_by: <you>`. To reject or ask for more, set `status: rejected` and add a line to `decisions.md`; the Planner runs again as attempt 2 (`--attempt 2`).
9. **Optional, to try the learning loop:** set `inspect-adapt` to `enabled: true` in the manifest and launch that lane the same way. It is off by default for spikes.

## What to look at

**Telemetry (the main unknown).**
- Is there a file in `otel-dump\SPIKE-1\run-1\`? If not, the Copilot CLI did not export: check its OpenTelemetry settings in the CLI documentation against the variable names in `scripts\wf_launch.py` (`--print-env` shows them).
- `otel_status` in the metrics file: `ok`, `incomplete` or `missing`. Read `problems` for what was not found.
- Do the `workflow.*` tags come through? If the tags are missing, `OTEL_RESOURCE_ATTRIBUTES` is not being used by the CLI.
- Are token counts present and plausible? Are there any credit-like attributes (`credit_attributes_found`)?
- Keep one real dump file and note the actual field names. The check was corrected against the first real dump (see below).

**Workflow.**
- Did the agent follow the lane instructions and produce `findings.md` in the right format? Did it stay out of the manifest?
- Was `wf_status` correct and useful at each step? Did its next-step hints match what you needed to do?
- How many manual steps were there, and which ones were tedious? This decides what to automate first.
- How long did it take compared with doing it without the workflow, and was the result as good?

## What the first real dump showed (Copilot CLI 1.0.x, file exporter)

- One JSON object per line: `span` lines and cumulative `metric` lines. The `workflow.*` tags are in `resource.attributes`, so tagging through `OTEL_RESOURCE_ATTRIBUTES` works.
- There is no `session.id`. The session id is `gen_ai.conversation.id`, on every span.
- Spans: `chat <model>` (one per model call), `execute_tool <tool>` (one per tool call), `permission`, and one `invoke_agent` that repeats the session totals. Summing every span double counts: the check now sums the `chat` spans only.
- `gen_ai.usage.input_tokens` already includes the cache read and write tokens, which are reported separately.
- Cost is present: `github.copilot.cost` and `github.copilot.nano_aiu` on each chat span and on `invoke_agent`. `nano_aiu / 1e9` gives AI units. How that maps to billing is not confirmed yet.
- Launching and quitting the CLI without a prompt writes a tiny dump (one notification span). Only the latest launch for a lane and attempt is checked.

## Pilot log (copy into a file and fill in)

```
Spike:
Date:
Question:
Without the workflow I would have:
Agent session length / time box:
Telemetry: dump found? tags present? tokens? credits? field names seen:
Findings quality (1-5) and why:
Manual steps that were tedious:
Wrong or unclear instructions in the lane files:
What I would change first:
```
