# ADR-016: Windows machine state broker for autonomous engineering

- Status: Proposed
- Date: 2026-11-17
- Decision owners: repository maintainers

## Context

The Windows dashboard and the self-hosted Runner must share one durable autonomous-engineering state store. The previous implementation granted the Runner account Modify and a configured application account Read/Execute on the state directory. That design failed when the app was launched by another Windows account, and the app's readiness probe and pending-work lock also required write access despite the app otherwise being read-only. Granting the shared directory to Everyone, Users, or Authenticated Users would remove the account dependency by exposing the durable files directly, which is not acceptable.

## Decision

1. Keep one machine-scoped state root under %ProgramData%\PersonalAIAssistant\autonomous-engineering (or one explicit absolute machine environment override). Do not derive it from a user's home, the application checkout, or the install directory.
2. Keep the GitHub Runner as a dedicated non-administrator service identity. The Runner continues to write the existing state files and acquire the existing byte-range locks directly.
3. Install PersonalAIAssistantStateBroker as a LocalService Windows service under %ProgramData%\PersonalAIAssistant\StateBroker. It reads and performs only fixed operations over a local named pipe. The service shares the existing autonomous-durable-state.lock protocol with Python and PowerShell.
4. Reset ACLs recursively across the complete state tree. Only the resolved Runner SID and LocalService retain Modify access. No app account, Users group, Everyone, or Authenticated Users ACE is added to the data directory.
5. The named pipe is limited to authenticated local Windows sessions and rejects remote clients. Its protocol accepts only versioned, allow-listed operations: storage health, pending-work status, progress snapshot, a numeric run-diagnostics identifier, and bounded temporary telemetry cleanup. Clients cannot submit file paths, enumerate directories, or read checkpoint/task/PR files directly. Caller identity is inspected through Windows pipe impersonation.
6. The dashboard uses the broker on Windows for readiness, marker checks, progress, diagnostics, and telemetry cleanup. POSIX keeps the existing direct shared-site-data design. The Runner/workflow write path, checkpoint watcher, task/PR ownership state, and Python/PowerShell lock protocol remain unchanged.

## Consequences and security boundary

- Any authenticated local Windows account can launch the application without a per-account ACL change or a configured app username. The account can access only the broker's fixed application protocol; it does not receive direct filesystem permission to the durable state.
- The pipe endpoint is intentionally available to authenticated local accounts because the app is expected to run under arbitrary local accounts. This is not a grant to the state directory. The endpoint must remain allow-listed and must not grow a generic file read/write or path operation.
- Local administrators and SYSTEM retain their normal machine-level administrative authority; the design does not claim to defend local data against a malicious administrator.
- The service is machine-local and not a distributed lock. The unique autonomous-state-owner Runner label remains required for host affinity.
- Provisioning is idempotent: it preserves an existing configured Runner, builds the broker to a temporary executable before replacing the installed broker, verifies LocalService service identity, and health-checks the pipe.
- If the broker is unavailable or the state lock cannot be acquired, dashboard admission fails closed. Existing task, PR, and checkpoint files are preserved.

## Validation requirements

- Compile the broker source against the Windows .NET Framework runtime on Windows CI.
- Parse all PowerShell provisioning and check scripts.
- Test that the Python client rejects unknown methods and path-bearing requests before opening the pipe.
- Test that the Windows state tree ACL helper removes stale app-user ACEs recursively and grants only Runner and LocalService.
- Run the Python state/progress, pending-task/PR, checkpoint ownership/recovery, and dispatch-safety test suites.
- On the target host, verify the broker can read/write the root as LocalService, the Runner can write state directly, and two different non-admin app accounts can independently call health/progress/pending-work and see the same persisted state.
- Confirm that neither app account can open/read state files directly, that a remote machine cannot connect to the named pipe, and that cleanup refuses to run while pending task/PR/cleanup markers exist.

No change is accepted as operationally complete until the target-host multi-account and denial tests pass.
