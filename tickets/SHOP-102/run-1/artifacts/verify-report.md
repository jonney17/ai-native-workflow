---
artifact: verify-report.md
ticket: SHOP-102
run: 1
author: reviewer
inputs: [artifacts/test-plan.md, artifacts/changes/shop-web/summary.md]
status: draft
---
# Verify report (attempt 3)

Result: **PASS**

## Coverage matrix
| AC | Repo | Test | Result |
|----|------|------|--------|
| AC-1 | shop-web | tests/shop-web/test_acceptance.py::test_ac1_free_shipping_banner_shows_remaining_amount | pass |
| AC-2 | shop-web | tests/shop-web/test_acceptance.py::test_ac2_free_shipping_banner_hidden_at_or_above_threshold | pass |

## Failures
