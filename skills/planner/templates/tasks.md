---
artifact: tasks.md
ticket: <TICKET>
run: <N>
author: <agent identity>
inputs: [ticket-context.md, design.md]
status: draft
---
<!-- Template. Keep the headings and the machine-read parts (marked MACHINE). Delete these comments. -->
# Tasks

## Tasks
<!-- MACHINE: repo names must match config/repos.yaml. Dev fans out from the repos listed here. Covers lists the ticket's criterion ids (J-n, or C-n for a chore); every id must be covered by a task. -->
| Task | Repo | Covers | Depends on |
|------|------|--------|------------|
| T-1 ... | <repo-name> | J-1 | none |

## Release
<!-- MACHINE: the orchestrator copies these keys into the manifest. -->
deploy_order: independent
<!-- Use `deploy_order: ordered` and the next line only when the order matters. -->
sequence: <repo-a>, <repo-b>
reason: <why this order, for example an API provider before its consumer>
