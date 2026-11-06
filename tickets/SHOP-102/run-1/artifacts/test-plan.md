---
artifact: test-plan.md
ticket: SHOP-102
run: 1
author: qe
inputs: [spec.md, tasks.md]
status: draft
---
# Test plan

## Coverage matrix
| AC   | Level | Repo | Test |
|------|-------|------|------|
| AC-1 | unit | shop-web | tests/shop-web/test_acceptance.py::test_ac1_free_shipping_banner_shows_remaining_amount |
| AC-2 | unit | shop-web | tests/shop-web/test_acceptance.py::test_ac2_free_shipping_banner_hidden_at_or_above_threshold |

## Integration scenarios
- No cross-repo scenarios are required for this ticket; the behavior is fully contained in the shop-web cart experience.

## Risks and gaps
- No spec gaps identified; both acceptance criteria are directly testable from the approved behavior.