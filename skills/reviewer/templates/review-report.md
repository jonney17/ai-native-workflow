---
artifact: review-report.md
ticket: <TICKET>
run: <N>
author: <agent identity>
inputs: [test-plan.md, changes]
status: draft
---
human_required: false
signals: []
<!-- MACHINE: the two lines above must come first, right after the header. human_required is true or false.
     signals lists the matched names from config/risk-rules.yaml. The orchestrator (or a person) copies the decision to review.human_required in the manifest. -->

Verdict: pass
<!-- MACHINE: verdict is pass, rework or design_rejected. On rework or design_rejected also append review_rejected (scope=code or scope=design). -->

## Review
<!-- Code against spec.md and design.md: correctness, scope creep, security, maintainability. -->

## Risk signals matched (config/risk-rules.yaml)
<!-- One line per matched signal with the evidence. Write "none" if nothing matched. -->
