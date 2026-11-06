---
artifact: tasks.md
ticket: SHOP-101
run: 1
author: planner
inputs: [ticket-context.md, spec.md, design.md]
status: draft
---
# Tasks

## Tasks
| Task | Repo | Covers | Depends on |
|------|------|--------|------------|
| T-1 Add tracking number to shipped order API responses | shop-api | AC-1 | none |
| T-2 Display the tracking number on shipped order pages | shop-web | AC-2 | T-1 |

## Release
deploy_order: ordered
sequence: shop-api, shop-web
reason: The web application consumes the order API response, so the API must be deployed first.
