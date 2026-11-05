---
artifact: verify-report.md
ticket: <TICKET>
run: <N>
author: <agent identity>
inputs: [test-plan.md, changes]
overlay_applied: []
status: draft
---
<!-- Template. Keep the headings and the machine-read parts (marked MACHINE). Delete these comments. -->
# Verify report (attempt <N>)

<!-- MACHINE: one line, exactly this form. -->
Result: **PASS**
<!-- or: Result: **FAIL** -->

## Coverage matrix
<!-- An AC with no passing test is a failure. -->
| AC   | Repo | Test | Result |
|------|------|------|--------|
| AC-1 | <repo> | test_ac1_... | pass |

## Failures
<!-- Only when the result is FAIL: one line per failing repo with its AC ids. Append verify_failed to events.log for each. -->
