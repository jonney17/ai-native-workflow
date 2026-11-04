---
artifact: tasks.md
ticket: <TICKET>
run: <N>
author: <agent identity>
inputs: [ticket-context.md@<hash>, spec.md@<hash>, design.md@<hash>]
overlay_applied: []
status: draft
---
<!-- Template. Keep the headings and the machine-read parts (marked MACHINE). Delete these comments. -->
# Tasks

## Tasks
<!-- MACHINE: repo names must match config/repos.yaml. Dev fans out from the repos listed here. -->
| Task | Repo | Covers | Depends on |
|------|------|--------|------------|
| T-1 ... | <repo-name> | AC-1 | none |

## Release
<!-- MACHINE: the orchestrator copies these keys into the manifest. -->
deploy_order: independent
<!-- Use `deploy_order: ordered` and the next line only when the order matters. -->
sequence: <repo-a>, <repo-b>
reason: <why this order, for example an API provider before its consumer>
