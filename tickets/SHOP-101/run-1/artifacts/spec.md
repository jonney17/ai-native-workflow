---
artifact: spec.md
ticket: SHOP-101
run: 1
author: planner
inputs: [ticket-context.md]
status: draft
---
# Spec: Show order tracking numbers

## Behavior
For shipped orders, the order API provides the tracking number and the order page displays it. The tracking number is not required to be available for orders that have not shipped.

## Acceptance criteria
| Id   | Criterion (given / when / then) | PRD requirement | Source |
|------|----------------------------------|-----------------|--------|
| AC-1 | Given an order has shipped and has a tracking number, when its order details are requested, then the order API response includes that tracking number. | PRD-7#R2 | J-1 |
| AC-2 | Given an order has shipped and has a tracking number, when the customer views the order page, then the page displays that tracking number. | PRD-7#R2 | J-2 |

## Traceability
| Source id | Spec AC | Status | Reason |
|-----------|---------|--------|--------|
| J-1       | AC-1    | kept   |        |
| J-2       | AC-2    | kept   |        |

## Out of scope
Tracking links, carrier integrations, and displaying tracking numbers for orders that have not shipped.
