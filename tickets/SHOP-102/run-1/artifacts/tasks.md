---
artifact: tasks.md
ticket: SHOP-102
run: 1
author: planner
inputs: [ticket-context.md, spec.md, design.md]
status: draft
---
# Tasks

## Tasks
| Task | Repo | Covers | Depends on |
|------|------|--------|------------|
| T-1 Implement the cart free-shipping banner and its threshold behavior. | shop-web | AC-1, AC-2 | none |

## Release
deploy_order: independent
reason: Only shop-web is affected, so no cross-repository deployment ordering is required.
