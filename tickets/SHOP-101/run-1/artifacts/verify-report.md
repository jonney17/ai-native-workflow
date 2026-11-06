---
artifact: verify-report.md
ticket: SHOP-101
run: 1
author: reviewer
inputs: [artifacts/test-plan.md, artifacts/changes/shop-api/summary.md, artifacts/changes/shop-web/summary.md]
status: ready
---
# Verify report (attempt 1)

Result: **PASS**

## Coverage matrix
| AC   | Repo     | Test                                                        | Result |
|------|----------|-------------------------------------------------------------|--------|
| AC-1 | shop-api | tests/shop-api/test_acceptance.py::test_ac1_tracking_number_in_order_api | pass |
| AC-2 | shop-web | tests/shop-web/test_acceptance.py::test_ac2_tracking_number_displayed_on_order_page | pass |

## Failures
