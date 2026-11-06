---
artifact: tasks.md
ticket: SHOP-104
run: 1
author: planner
inputs: [ticket-context.md, spec.md, design.md]
status: draft
---
# Tasks

## Tasks
| Task | Repo | Covers | Depends on |
|------|------|--------|------------|
| T-1 Expose order history in a CSV-friendly API payload with date, number and total. | shop-api | AC-1, AC-2 | none |
| T-2 Add the Export CSV action to the account page and trigger the download. | shop-web | AC-1, AC-2 | T-1 |

## Release
deploy_order: ordered
sequence: shop-api, shop-web
reason: The web UI consumes the order data contract, so the API must be deployed before the account-page export action.
