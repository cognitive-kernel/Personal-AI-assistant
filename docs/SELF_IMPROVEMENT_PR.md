# Governed self-improvement: draft PR publication

The self-improvement workflow can publish an **approved proposal as a GitHub draft pull request**. It does not merge, deploy, or apply the candidate patch to the application's working checkout.

## Required configuration

Set these environment variables in the local application process:

- `SELF_IMPROVEMENT_GITHUB_REPOSITORY`: allowlisted target in `owner/repository` form.
- `SELF_IMPROVEMENT_GITHUB_TOKEN`: a fine-grained token limited to that repository with **Contents: read/write** and **Pull requests: read/write**. Do not put the token in the browser, database, source control, command-line arguments, or logs.
- `SELF_IMPROVEMENT_GITHUB_BASE_BRANCH`: optional; defaults to `main`.

If the token or repository is not configured, publication fails closed. The API sends the token only in the HTTPS Authorization header and never returns it.

## Publication gates

A publication request requires all of the following:

1. The proposal was explicitly approved for PR review.
2. The patch hash matches the recorded proposal hash.
3. The local checkout HEAD still matches the validated base commit.
4. Isolated syntax checking, test-integrity checks, and behavioral tests all passed for the same patch and base.
5. The configured remote base branch still points to that exact base commit.
6. The user explicitly confirms the draft PR creation action.

The candidate is applied only in a temporary detached Git worktree. Changed text files are sent to GitHub as blobs, then a tree and commit are created with the validated base as parent. Only allowlisted paths are published. If PR creation fails after a branch was created, the branch is deleted when safe to do so. A successful PR is recorded in the proposal event history, and repeated requests return the existing PR instead of creating another.

## Non-goals and limits

- PRs are always created as **drafts**.
- This feature never merges, deploys, or changes the local checkout.
- The human reviewer remains responsible for semantic correctness, security, and whether a patch should be merged.
- The test-integrity guard is heuristic, not a proof that tests cannot be weakened.
- GitHub credentials are optional configuration; no remote publication occurs without them and explicit user confirmation.
- A failed API call can leave a branch behind if GitHub accepted the branch but the network failed before the client received confirmation. Inspect the proposal's GitHub refs before retrying in that rare case.
