---
artifact: test-plan.md
ticket: <TICKET>
run: <N>
author: <agent identity>
inputs: [ticket-context.md, tasks.md]
status: draft
---
<!-- Template. Keep the headings and the machine-read parts (marked MACHINE). Delete these comments. -->
# Test plan

## Coverage matrix
<!-- MACHINE: every criterion id from ticket-context.md (J-n, or C-n for a chore) appears in at least one row. The Reviewer uses the same ids. -->
| Id   | Level | Repo | Test |
|------|-------|------|------|
| J-1  | unit  | <repo> | tests/<repo>/test_acceptance.py::test_j1_... |

## Integration scenarios
<!-- Placeholder for now: list given / when / then scenarios that cross repos. Automate under tests/integration/ if you can; the Reviewer runs those and lists the rest as manual. -->
- Given ..., when ..., then ...

## Risks and gaps
<!-- ACs that are hard to test, and why. Raise question_raised if a criterion is unclear, or design_gap if the design or tasks block testing. -->
