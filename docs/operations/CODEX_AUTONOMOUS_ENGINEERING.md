# Autonomous Codex engineering workflow

## Purpose

`.github/workflows/autonomous-codex-engineering.yml` runs a bounded Codex engineering session on a GitHub-hosted Linux runner. It is an event-driven, periodic worker, not a continuously running 24/7 process.

## Activation requirements

Before the first run:

1. Create an OpenAI API key for the Responses API. ChatGPT subscription access is not an API key and does not automatically provide API billing.
2. In **Settings → Secrets and variables → Actions**, create a repository secret named `OPENAI_API_KEY`. Never place the key in source control, workflow inputs, issue text, or logs.
3. In **Settings → Actions → General → Workflow permissions**, allow GitHub Actions to create pull requests. The workflow needs this only in its final PR-publication job.
4. Merge the workflow PR to `main`. Scheduled workflows run from the default branch.

If the API secret is missing, the agent job fails closed before invoking Codex.

## Triggers

- Scheduled run every six hours (GitHub Actions scheduling is best-effort and may be delayed).
- Manual `workflow_dispatch`.
- Every push to `main` (including merged engineering PRs).
- Manual `workflow_dispatch`, with an optional task request.

This does not promise uninterrupted execution. GitHub-hosted jobs are finite, scheduled events can be delayed, and GitHub may disable scheduled workflows after prolonged repository inactivity.

## Permission separation

| Job | Permissions | Receives OpenAI key | Executes candidate code |
|---|---|---:|---:|
| Codex | `contents: read` | Yes, only through the Codex Action input/proxy | No privileged GitHub write |
| Validation | `contents: read` | No | Yes, tests the candidate patch |
| Draft PR publication | `contents: write`, `pull-requests: write` | No | No project tests or candidate scripts |

Codex uses the pinned OpenAI Codex GitHub Action, the `:workspace` permission profile, and the Linux `drop-sudo` safety strategy. Its candidate patch is restricted to `app/`, `tests/`, `docs/`, and `pyproject.toml`.

The patch is transferred as an artifact to a separate job. That job applies it to the exact base commit, installs dependencies without the OpenAI secret, and runs the Linux test suite plus the repository's generated-artifact, Markdown-link, import-cycle, retrieval, and claim-to-test checks. Only after all configured checks pass does a separate write-enabled job create a **draft pull request**.

The workflow never writes to `main`, merges, deploys, changes branch protections, modifies secrets, or performs destructive database operations. Normal PR CI runs on generated PRs. **At the time this workflow was added, `main` had no branch protection or required status checks**, so review/check enforcement is not guaranteed by GitHub settings; configure a repository ruleset/branch protection separately before relying on those gates. The draft-only behavior is a workflow convention, not an enforced merge lock. A passing Linux validation job does not replace Windows, browser, security, target-host, or human acceptance gates.

## Operational controls and limitations

- The agent follows `docs/MASTER_ENGINEERING_PROMPT.md` and updates the existing engineering log.
- Manual task text and repository/test output are untrusted task data; they cannot override the engineering/security protocol.
- The OpenAI key is not available to the validation or PR-publication jobs.
- The PR job requires the repository setting that permits Actions to create pull requests.
- API usage is billed separately from a ChatGPT subscription. Set appropriate API project budgets/limits before enabling recurring runs.
- The six-hour schedule is a practical periodic cadence, not a guarantee of 24/7 availability. More frequent/event-driven runs can consume additional API and Actions minutes.
- The workflow is intentionally not enabled for `pull_request_target` or arbitrary fork PRs, and it does not automatically merge generated changes.

## First-run checklist

- [ ] Add the `OPENAI_API_KEY` repository Actions secret.
- [ ] Enable Actions to create pull requests.
- [ ] Merge the workflow PR after review and required checks.
- [ ] Run the workflow manually once and inspect its logs.
- [ ] Confirm a generated draft PR receives all normal repository CI checks.
- [ ] Keep the draft PR unmerged until normal review and repository acceptance gates are satisfied.
