# Governed self-improvement GitHub API boundary

The self-improvement workflow can publish an explicitly approved draft pull request. Its GitHub token is a sensitive credential and must remain scoped to the fixed GitHub API origin.

## Transport controls

- Requests are constructed only for `https://api.github.com` using the configured owner/repository path.
- Environment-configured HTTP(S) proxies are disabled so an ambient proxy cannot receive the bearer token or change the effective destination.
- Redirects are rejected. A redirect must never forward the bearer token or patch metadata to another origin.
- Response reads remain bounded to 1 MiB, and provider error bodies are not propagated because they may contain sensitive metadata.
- PR publication still requires explicit confirmation and creates a draft PR only; it does not merge or deploy.

## Regression coverage

`tests/test_self_improvement_pr.py::test_github_api_disables_environment_proxies_and_redirects` verifies the fixed API origin, bearer header placement, disabled proxy configuration, redirect rejection, timeout, and bounded response read without making a live GitHub request.
