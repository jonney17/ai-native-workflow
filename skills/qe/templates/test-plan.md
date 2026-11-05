---
artifact: test-plan.md
ticket: <TICKET>
run: <N>
author: <agent identity>
inputs: [spec.md, tasks.md]
overlay_applied: []
status: draft
---
<!-- Template. Keep the headings and the machine-read parts (marked MACHINE). Delete these comments. -->
# Test plan

## Coverage matrix
<!-- MACHINE: every AC id from spec.md appears in at least one row. The Reviewer uses the same AC ids. -->
| AC   | Level | Repo | Test |
|------|-------|------|------|
| AC-1 | unit  | <repo> | tests/<repo>/test_acceptance.py::test_ac1_... |

## Integration scenarios
<!-- Placeholder for now: list given / when / then scenarios that cross repos. Automate under tests/integration/ if you can; the Reviewer runs those and lists the rest as manual. -->
- Given ..., when ..., then ...

## Risks and gaps
<!-- ACs that are hard to test, and why. Raise spec_gap if the spec itself is unclear. -->
