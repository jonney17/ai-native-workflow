# Reviewer lane

Verify, then review. You must not be the agent identity that produced any `changes/*` or `tests/*`.

## Requires
`changes/*` (all repos in `tasks.md`), `test-plan.md`.

## Produces
`verify-report.md`, `review-report.md`.

## Steps
1. **Verify.** Apply Dev's changes and QE's tests, run them, and record results in `verify-report.md` with the coverage matrix: AC id, tests, pass or fail. An AC with no passing test is a failure.
   The paths of all registered repos are in `WF_REPO_PATHS` (JSON). If `test-plan.md` has integration scenarios, run the automated ones from `tests/integration/` and list the rest as manual (placeholder, to be improved).
2. If verification fails, append `verify_failed` with the failing repo and AC ids, write the report, and stop. Only that repo's Dev lane re-runs.
3. **Review.** Check the code against `spec.md` and `design.md`: correctness, scope creep, security, maintainability.
   For a behavior-preserving chore (see `ticket-context.md`), no existing test expectation may change. Flag any that do.
4. **Risk.** Evaluate `config/risk-rules.yaml` against Dev's `notes.md` and the diffs. List which signals matched. If any matched, the review needs a human.
   Put the decision on the first lines of `review-report.md`: `human_required: true` or `human_required: false`, followed by the matched signals. You propose it; whoever applies the transition (the orchestrator, or a person by hand for now) copies it to `review.human_required` in the manifest. `wf_status.py` reports the ticket as waiting until it is set. For `multi_repo_ordered_deploy`, read the Release section of `tasks.md`.
5. Write `review-report.md`: verdict (`pass`, `rework`, or `design_rejected`), reasons, matched risk signals. On `rework` or `design_rejected`, append `review_rejected` with `scope=code` or `scope=design`.

## Templates

Start from `skills/reviewer/templates/verify-report.md` and `skills/reviewer/templates/review-report.md`. Keep the parts marked MACHINE: the `Result:` line in the verify report, and in the review report the first lines (`human_required:` and `signals:`) and the `Verdict:` line. `wf_status.py` warns when one is missing or when `human_required` in the manifest disagrees with the report.

## Done when
`verify-report.md` shows every AC covered by a passing test, and `review-report.md` has a verdict.

## Do not
Fix the code yourself. Report it; Dev fixes it.
