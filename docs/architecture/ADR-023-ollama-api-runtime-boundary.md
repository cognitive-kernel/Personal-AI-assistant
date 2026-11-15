# ADR-023 — Ollama API runtime boundary for Windows service identities

- **Status:** Proposed; repository implementation pending exact-head CI
- **Date:** 2026-11-15
- **Scope:** Windows autonomous Runner, provisioning, and readiness diagnostics

## Context

The Windows autonomous workflow runs under a dedicated non-administrator service identity. The workflow previously required `Get-Command ollama` and executed `ollama --version` before checkpoint recovery and again before Codex startup. Runner provisioning also required the Ollama executable to be present in that identity's PATH.

The official Ollama Windows documentation states that the standard installer installs under the installing user's home directory by default and adds its binary directory to that user's PATH. The application can be installed elsewhere with the installer's `/DIR` option; a separate standalone CLI archive is also documented for service integration. Therefore, a normal per-user installation can be healthy while the service identity cannot resolve its CLI. The failure is a mismatch between the project's runtime contract and the Windows installer scope, not proof that the Ollama server or selected model is unavailable.

The autonomous workflow already uses the local Ollama HTTP API for model inventory, metadata, context creation, runtime allocation, and tool-call verification. Requiring a second, user-scoped CLI path duplicates discovery and blocks the existing API-based runtime.

## Decision

1. The autonomous Runner's Ollama dependency is the loopback API at `http://127.0.0.1:11434`, not the CLI executable or a user-profile installation path.
2. `scripts/AutonomousOllamaRuntime.psm1` owns the small shared PowerShell contract for reading `/api/tags`, validating the response shape and each model name, and checking exact model presence. Callers must not duplicate this catalog parsing.
3. The API helper uses a fixed loopback IP literal, bounded request timeout, and disabled redirects. It does not accept arbitrary endpoints, credentials, proxies, or remote hosts.
4. API-unavailable, malformed-catalog, and model-not-installed conditions remain distinct failures. No missing or invalid value is replaced with a guessed model.
5. Workflow startup, Runner provisioning, and Runner/app readiness checks must not require `ollama.exe` in the service account's PATH. The Ollama CLI may remain available for operator diagnostics or interactive application setup, but it is not an autonomous Runner prerequisite.
6. Model metadata, tool capability, persistent Codex context, actual `/api/ps` allocation, and the real workspace-confined Codex smoke remain mandatory. Removing the CLI prerequisite does not weaken these checks or the loopback-only network boundary.
7. The application launcher may continue resolving the current interactive user's Ollama installation dynamically; that is separate from the service Runner's API-only contract. No fixed username, profile path, SID, or machine-specific executable location is introduced.

## Failure and recovery semantics

- If the local API is unavailable, readiness fails before Codex starts and reports the loopback API failure.
- If the API responds with a malformed model catalog, readiness fails closed.
- If the selected model is absent, readiness reports that exact model as missing; it does not install, substitute, or silently change the selection.
- The existing task-state lock, repository execution lock, checkpoint-store lock, watcher ownership, process-tree supervision, retry budget, and circuit breaker remain unchanged. Ollama discovery failure must not mutate or clear durable task/checkpoint state.

## Verification

- PowerShell contract tests cover valid catalog normalization, exact model lookup, missing `models`, and unnamed model entries.
- Static regressions reject `Get-Command ollama` and `ollama --version` as workflow/provisioning/readiness requirements.
- Windows CI parses the module and all PowerShell scripts, and executes the pure catalog-contract tests.
- Exact-head full tests, Windows storage/process/checkpoint regressions, Browser E2E, dependency audit, and CodeQL are required.
- A target-host run must still verify that the non-administrator Runner can reach the already-running loopback API, inspect the selected model, complete context allocation, and execute the sandbox smoke. CI does not replace this operational acceptance.

## References

- [Ollama Windows installation documentation](https://github.com/ollama/ollama/blob/main/docs/windows.mdx)
- [Ollama API documentation](https://github.com/ollama/ollama/blob/main/docs/api.md)
