---
artifact: spec.md
ticket: <TICKET>
run: <N>
author: <agent identity>
inputs: [ticket-context.md]
overlay_applied: []
status: draft
---
<!-- Template. Keep the headings and the machine-read parts (marked MACHINE). Delete these comments. -->
# Spec: <short title>

## Behavior
<!-- Two to four sentences in plain language: what changes for the user or the system. -->

## Acceptance criteria
<!-- MACHINE: ids are AC-1, AC-2, ... QE, the Reviewer and wf_status.py match tests to these ids. -->
| Id   | Criterion (given / when / then) | PRD requirement | Source |
|------|----------------------------------|-----------------|--------|
| AC-1 | Given ..., when ..., then ...    | PRD-0#R0 (n/a for a chore) | J-1 (or C-1, or `added`) |

## Traceability
<!-- MACHINE: this heading is required. Every source criterion appears once. A reason is required for changed and dropped. -->
| Source id | Spec AC | Status | Reason |
|-----------|---------|--------|--------|
| J-1       | AC-1    | kept   |        |
<!-- Status is kept, changed, dropped or added. -->

## Out of scope
<!-- What this ticket deliberately does not do. -->
