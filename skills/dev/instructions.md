# Dev lane (one instance per repo)

Build the change for your repo only.

## Requires
`spec.md@approved`, `design.md@approved`, `tasks.md@approved`.

## Produces
`changes/<repo>/` containing `diff.patch`, `notes.md`, `local-verify.md`.

## Steps
1. Read only your repo's section of `tasks.md`, plus the spec and design. Your repo is the working directory (`WF_REPO_PATH`).
2. Implement on a branch named `wf/<TICKET>-run<N>`. Follow the repo's own conventions and existing tests.
3. Run the repo's build and existing tests locally. Record the commands and results in `local-verify.md`.
4. Write `notes.md`: what changed and why, files touched, changed line count, and risk signals (API contract, schema, auth, CI or dependency changes). The Reviewer uses these against `config/risk-rules.yaml`.

## Done when
- Build and existing tests pass locally, or failures are explained and unrelated.
- Every task for your repo is done or marked blocked with a reason.

## Do not
Touch other repos or `tests/` (QE owns it). If the spec is wrong or incomplete, append `spec_gap` with the AC id and stop that task; do not work around it.
