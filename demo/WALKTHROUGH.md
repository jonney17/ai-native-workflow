# Walkthrough: what to say and show

About 15 minutes. Run one step at a time with `python demo/demo.py next` and show the status that follows. Run `python demo/demo.py reset` before you start.

Say at the start: *The agents here are scripted stand-ins, so you see the same run every time. The launcher, the status view, the artifact rules and the telemetry check are the real ones. The repos are fake.*

| Step | Run | Show | Say |
|------|-----|------|-----|
| 0 | `setup` | The two fake repos exist, no tickets. | Each Jira ticket is one run of this flow. Today's ticket: show the tracking number on the order page, touching two repos. |
| 1 | `next` | Status is PLANNING, only the Planner is ready. | Status is never typed. It is derived from the lanes and artifacts. Only one lane can start, because only one has what it needs. |
| 2 | `next` | The Planner's files: spec (ACs and a traceability table), design, tasks with a Release section. | The Planner adopted the Jira criteria and added one AC of its own, AC-5, and said why. It also found the two repos and wrote the deploy order. |
| 3 | `next` | Status flips to BUILDING; the manifest now lists the repos. | First human gate. One approval covers spec, design and tasks. This is the last human input until risk shows up. |
| 4 | `next` | Three agents start at once; the status shows them all done. | QE and two Devs work in parallel from the same approved plan, with no hand-offs. Each Dev has its own branch in its own repo. |
| 5 | `next` | The failing AC-4 and AC-5 in the verify report, and `changes/shop-web rejected`. | The Reviewer is independent of Dev and QE. It found a real bug: the page prints "Tracking: None". It proposes a rework, and only shop-web goes back. shop-api is not touched. |
| 6 | `next` | `verify-report.md` is STALE after the rework. | We never told the Reviewer to go again. The old report was built on old changes, so it is stale, and stale means re-queue. |
| 7 | `next` | `review-report.md` first lines: `human_required: true` and the two signals. | The Reviewer applies the risk rules: ordered multi-repo deploy and a changed API. That is why a human is asked. A low-risk ticket would skip this gate. |
| 8 | `next` | Status moves to DEPLOYING. | Second gate, only because of risk. |
| 9-10 | `next`, `next` | `git log --graph` in `demo/workspace/repos/shop-web`; the release order in the status. | Deploy is plain CI/CD, not a lane. It follows the order from tasks.md. Trying shop-web first is refused. |
| 11 | `next` | Status is DONE. | The acceptance policy is still TBD. The demo uses our suggested default: accepted by evidence, with a human sign-off because the review was risk-flagged. |
| 12 | `next` | `ledger/L-2026-11-001.yaml` and `overlay/dev/L-2026-11-001.md`. | After acceptance, Inspect & Adapt reads the trail and telemetry. It saw one rework loop caused by missing null tests, logged it, and wrote a small removable candidate for the Dev lane. The sprint retro decides whether it is promoted to the team skills. |
| 13 | `next` | `workflow.overlay=L-2026-11-001` in the printed environment. | The next Dev session carries the candidate and is tagged with it, so we can compare tickets with and without it. |

## Optional detour: stale cascade

Between steps 5 and 10, run `python demo/demo.py stale on`. It edits the approved spec. Show how the Planner, QE, both Dev lanes and the Reviewer become stale, each with its reason. Say: *One edit upstream and everything built on it is marked, so nobody ships against a spec that moved.* Then `stale off`. The driver refuses `next` while the detour is on.

## Questions to expect

- **Is this a real agent?** No. The stub writes scripted text. The real scripts and rules are running, and a real lane is started the same way (`wf_launch.py ... -- <your command>`).
- **Who applies the transitions?** The demo driver plays the orchestrator. In the real system this is still to be built; today a person does it by hand and records it in `decisions.md`.
- **Are the token numbers real?** No. They are synthetic. Whether real Copilot telemetry has everything we need is the first thing to check with a pilot.
- **Where can I help?** Placeholders to improve: cross-repo tests (QE), release order and partial-failure handling (Dev), lane instructions (everyone), risk thresholds (Reviewers). See `docs/workflow-design.md`.
