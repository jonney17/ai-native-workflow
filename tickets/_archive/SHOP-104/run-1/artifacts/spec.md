---
artifact: spec.md
ticket: SHOP-104
run: 1
author: planner
inputs: [ticket-context.md]
status: draft
---
# Spec: Export order history as CSV

## Behavior
Customers can export a CSV of their order history from the account page. The file contains one row per order and includes the order date, order number, and total, making it easy to save or share their purchase history.

## Acceptance criteria
| Id   | Criterion (given / when / then) | PRD requirement | Source |
|------|----------------------------------|-----------------|--------|
| AC-1 | Given a customer is on the account page, when they choose to export their order history, then an Export CSV button is available. | PRD-7#R6 | J-1 |
| AC-2 | Given a customer has order history, when they export it, then the CSV contains one row per order with the date, number, and total. | PRD-7#R6 | J-2 |

## Traceability
| Source id | Spec AC | Status | Reason |
|-----------|---------|--------|--------|
| J-1       | AC-1    | kept   |        |
| J-2       | AC-2    | kept   |        |

## Out of scope
Changing checkout, creating a shared export link, or adding email delivery for CSV files.
