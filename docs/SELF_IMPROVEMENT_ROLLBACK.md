# Governed rollback PRs

The local self-improvement UI can request a rollback **draft pull request** for a proposal whose original PR was published by this workflow and has since been merged.

## Preconditions

- The original proposal must have a recorded PR publication event.
- GitHub must report the original PR as merged and provide a merge commit SHA.
- The configured base branch must have advanced beyond the original base.
- The original base tree, merged tree, and current base tree must be readable without truncation.
- Every affected path must still have exactly the same blob SHA, file mode, and type as it had immediately after the original merge.

If any affected path changed after the original merge, the rollback request fails closed. It does not overwrite subsequent edits. If a path was created by the original patch, the rollback tree removes it; if it existed before the patch, the rollback tree restores its original blob. Only paths from the original validated patch manifest are considered.

## Result and safety boundary

A successful request creates a new commit on a dedicated ai-rollback/ branch and opens a draft PR for human review. The local checkout is not modified. The workflow never merges or deploys the rollback. Requests are idempotent once the rollback PR is recorded in the proposal event history.

The same SELF_IMPROVEMENT_GITHUB_REPOSITORY, SELF_IMPROVEMENT_GITHUB_TOKEN, and optional SELF_IMPROVEMENT_GITHUB_BASE_BRANCH configuration described in SELF_IMPROVEMENT_PR.md is used. Explicit user confirmation is required.

This guard only proves the affected file snapshots have not changed since the recorded merge. It does not prove the rollback is semantically correct, and human review remains mandatory.
