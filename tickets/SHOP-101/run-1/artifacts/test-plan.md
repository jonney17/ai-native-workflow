---
artifact: test-plan.md
ticket: SHOP-101
run: 1
author: qe
inputs: [spec.md, tasks.md]
status: draft
---
# Test plan

## Coverage matrix
| AC   | Level | Repo | Test |
|------|-------|------|------|
| AC-1 | unit | shop-api | tests/shop-api/test_acceptance.py::test_ac1_tracking_number_in_order_api |
| AC-2 | unit | shop-web | tests/shop-web/test_acceptance.py::test_ac2_tracking_number_displayed_on_order_page |

## Integration scenarios
- Given a shipped order with a tracking number, when the order API returns that order and the web page loads the same order data, then the customer sees the tracking number on the order page without showing it for unshipped orders.
- Given an unshipped order, when the order API response is requested or the order page is loaded, then no tracking number is shown and the field remains absent.

## Risks and gaps
- No spec gaps identified; both acceptance criteria are directly testable from the approved behavior.
