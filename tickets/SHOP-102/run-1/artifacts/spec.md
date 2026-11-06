---
artifact: spec.md
ticket: SHOP-102
run: 1
author: planner
inputs: [ticket-context.md]
status: draft
---
# Spec: Free-shipping banner in the cart

## Behavior
The cart informs customers below the free-shipping threshold how much more they need to spend to reach it. Once the threshold is reached, the banner is no longer shown.

## Acceptance criteria
| Id   | Criterion (given / when / then) | PRD requirement | Source |
|------|----------------------------------|-----------------|--------|
| AC-1 | Given the cart total is below the free-shipping threshold, when the customer views the cart, then the banner shows the amount remaining to reach the threshold. | PRD-7#R4 | J-1 |
| AC-2 | Given the cart total is at or above the free-shipping threshold, when the customer views the cart, then the free-shipping banner is not shown. | PRD-7#R4 | J-2 |

## Traceability
| Source id | Spec AC | Status | Reason |
|-----------|---------|--------|--------|
| J-1       | AC-1    | kept   |        |
| J-2       | AC-2    | kept   |        |

## Out of scope
Changing the free-shipping threshold or shipping policy.
