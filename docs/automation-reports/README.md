# Automation Run Reports

This directory is the durable, reviewable record of work performed by the three scheduled project automations.

## Required behavior

- Each automation creates exactly one report for each attempted run, even when blocked or when no code change is justified.
- Use a unique UTC timestamp in the filename, for example `docs/automation-reports/2026-10-12T201500Z-ai-learning.md`. Use lowercase slugs: `ai-learning`, `software-stability`, or `coordinator`.
- Reports must be committed to a dedicated branch and linked from the corresponding PR. Do not commit directly to `main`.
- A report is not proof by itself: link the exact commit, PR, CI run, test command/output, benchmark artifact, or other evidence. Separate observed facts from assumptions.
- If no PR/change is warranted, still commit the report on a report-only branch and open a small PR, unless access or a repository failure prevents it; in that case, document the blocker in the automation result.
- Never fabricate a run ID, timestamps, test results, CI state, or local execution. If no platform run ID is exposed, write `not exposed by automation platform`.
- The coordinator must not attribute commits to a worker unless the report/PR/commit evidence supports the attribution. It checks that the report is present, verifies links and exact-head CI, then updates `docs/PROJECT_PROGRESS.md` with a short evidence-backed summary.
- Do not have multiple automations edit the same report or dashboard concurrently. Workers own their unique report files; only the coordinator owns the dashboard.
- Do not merge PRs merely because a report exists. Apply the project's review, security, overlap, and CI policies.

## Report filename

`YYYY-MM-DDTHHMMSSZ-<workstream>.md`

## Template

Copy the contents of [TEMPLATE.md](TEMPLATE.md) and fill every field. Use `Not run`, `Unknown`, or `Blocked` where appropriate, with a reason; never leave a misleading implied success.

## Run status vocabulary

- `COMPLETED`: the stated bounded task and its acceptance criteria are evidenced as complete.
- `PARTIAL`: useful work was completed but one or more criteria remain.
- `BLOCKED`: work could not safely proceed; include the concrete blocker and attempted checks.
- `NO CHANGE JUSTIFIED`: repository inspection found no safe/value-positive change; record what was inspected and why.
- `UNVERIFIED`: the claim cannot be backed by available evidence.

A successful CI run validates only the checks it actually ran on the exact reported head; it does not imply feature completeness or production readiness.
