---
artifact: design.md
ticket: SHOP-101
run: 1
author: planner
inputs: [ticket-context.md, spec.md]
status: draft
---
Implement tracking-number availability in shop-api first, then render the returned value on the shipped-order view in shop-web. Keep the change limited to the existing order details flow; do not introduce carrier lookups or tracking-link behavior.
