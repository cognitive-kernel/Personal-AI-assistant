# ADR-016: Windows machine state broker for autonomous engineering

- Status: Proposed
- Date: 2026-11-17
- Decision owners: repository maintainers

## Context

The Windows dashboard and the self-hosted Runner must share one durable autonomous-engineering state store. The previous implementation granted the Runner account Modify and a configured application account Read/Execute on the state directory. That design failed when the app was launched by another Windows account, and the app's readiness probe and pending-work lock also required write access despite the app otherwise being read-only. Granting the shared directory to Everyone, Users, or Authenticated Users would remove the account dependency by exposing the durable files directly, which is not acceptable.

## Decision

1. Keep one machine-scoped state root under %ProgramData%\PersonalAIAssistant\autonomous-engineering (or one explicit absolute machine environment override). Do not derive it from a user's home, the application checkout, or the install directory.
2. Treat the GitHub Runner as an optional execution capability, not a prerequisite for the local dashboard or machine broker. When installed, it remains a dedicated non-administrator service identity and continues to write the existing state files and acquire the existing byte-range locks directly. Broker installation must also succeed when no Runner service is installed.
3. Install PersonalAIAssistantStateBroker as a LocalService Windows service under %ProgramData%\PersonalAIAssistant\StateBroker. It reads and performs only fixed operations over a local named pipe. The service shares the existing autonomous-durable-state.lock protocol with Python and PowerShell.
4. Reset ACLs recursively across the complete state tree. LocalService always retains Modify access; every explicitly discovered/selected Runner service identity is added when present. With no Runner installed, LocalService is the only application service identity granted Modify access. No app account, Users group, Everyone, or Authenticated Users ACE is added to the data directory. Resolve Runner identity from the Windows service StartName; do not require reading the protected `.runner` registration file.
5. The named pipe is limited to authenticated local Windows sessions and rejects remote clients. Its protocol accepts only versioned, allow-listed operations: storage health, pending-work status, progress snapshot, a numeric run-diagnostics identifier, and bounded temporary telemetry cleanup. Clients cannot submit file paths, enumerate directories, or read checkpoint/task/PR files directly. Caller identity is inspected through Windows pipe impersonation.
6. The dashboard uses the broker on Windows for readiness, marker checks, progress, diagnostics, path-free checkpoint status, and telemetry cleanup. POSIX exposes the same application contract through its restricted Unix-domain-socket broker and OS-wide machine data root. The Windows named-pipe service and POSIX socket service are platform adapters; the API and UI do not depend on a human username, a configured user/group name, a home directory, a checkout path, or a fixed CPU/GPU model. Service identities and machine paths are discovered from the host during provisioning; model context is selected from Ollama metadata and current resource limits. The underlying operating system still supplies the required local IPC, file locking, and service isolation primitives.

## Consequences and security boundary

- Any authenticated local Windows account can launch the application without a per-account ACL change or a configured app username. The account can access only the broker's fixed application protocol; it does not receive direct filesystem permission to the durable state.
- The pipe endpoint is intentionally available to authenticated local accounts because the app is expected to run under arbitrary local accounts. This is not a grant to the state directory. The endpoint must remain allow-listed and must not grow a generic file read/write or path operation.
- Local administrators and SYSTEM retain their normal machine-level administrative authority; the design does not claim to defend local data against a malicious administrator.
- The service is machine-local and not a distributed lock. The unique autonomous-state-owner Runner label remains required for host affinity.
- Broker provisioning is independent and idempotent: it does not create or register a Runner, builds the broker to a temporary executable before replacing the installed broker, verifies LocalService service identity, and health-checks the pipe. If Runner services exist, their service identities are discovered and their listeners are stopped/restarted around shared-state ACL reconciliation; active workers cause a fail-closed refusal. The separate Runner provisioner remains an explicit opt-in capability.
- If the broker is unavailable, a checkpoint pointer is corrupt, or durable-state inspection is inconclusive, dashboard admission fails closed. Existing task, PR, and checkpoint bytes are preserved. Completed checkpoints are deactivated through an atomic inactive pointer; retained historical snapshots cannot silently become active again.

## Validation requirements

- Compile the broker source against the Windows .NET Framework runtime on Windows CI.
- Parse all PowerShell provisioning and check scripts.
- Test that the Python client rejects unknown methods and path-bearing requests before opening the pipe.
- Test that the Windows state tree ACL helper removes stale app-user ACEs recursively and grants only LocalService plus discovered Runner identities when present.
- Test broker installation with zero Runner services and verify that LocalService alone can use the broker; separately verify that adding a Runner grants only its resolved service SID.
- Run the Python state/progress, pending-task/PR, checkpoint ownership/recovery, and dispatch-safety test suites.
- On the target host, verify the broker can read/write the root as LocalService, the Runner can write state directly, and two different non-admin app accounts can independently call health/progress/pending-work and see the same persisted state.
- Confirm that neither app account can open/read state files directly, that a remote machine cannot connect to the named pipe, and that cleanup refuses to run while pending task/PR/cleanup markers exist.
- Exercise active, inactive, stale, corrupt, and patch-size-mismatched checkpoint pointers through both Windows and POSIX brokers. Verify that status remains responsive while a checkpoint watcher holds its lifetime lock and that no patch contents or filesystem paths are exposed.

No change is accepted as operationally complete until the target-host multi-account and denial tests pass.


## 2026-11-23 addendum — core broker independent of Runner

The previous Windows bootstrap required exactly one registered Runner service and readable `.runner` metadata before it could install or repair the core state broker. This coupled dashboard state access to an optional execution capability and made protected Runner registration metadata an accidental prerequisite.

The revised installer discovers zero or more Runner services through their Windows service executable path and resolves each service identity from `Win32_Service.StartName`. It never opens `.runner`. With no Runner service, the broker is installed under LocalService and the shared-state ACL grants Modify only to LocalService. With Runner services present, their selected service SIDs are added. All discovered Runner listeners are stopped before ACL mutation, active workers are rechecked, and every stopped service is restarted in a finally path. An unresolved Runner executable path fails closed before ACL changes.

This addendum records the intended repository contract; operational acceptance still requires exact-head CI and target-host verification, including a machine with no Runner and a machine with the existing protected Runner registration metadata.

## 2026-11-25 addendum — explicit provisioning and setup ownership

- Application startup performs a read-only Broker health probe. Machine-level service installation and ACL reconciliation are initiated only by the explicit loopback-protected dashboard action.
- The elevated PowerShell child, not the non-elevated launcher, owns the machine-wide setup mutex. A protected HKLM recovery marker records abandoned ownership; the marker remains until setup, Runner restoration, and live Broker health verification all succeed. An explicit acknowledgement is required after inspecting service and ACL state.
- The service binary is built reproducibly in CI, hash-verified, provenance-attested and staged atomically. The target host does not compile C#; .NET Framework 4.8+ runtime and Windows service APIs are preflight capabilities.
- ACL reconciliation returns a rollback journal and restores original owner/group/ACE state on failure. No task/checkpoint or durable state is removed as part of Broker provisioning.
- CI and target-host validation are separate: Windows storage integration must prove mutex ownership, abandoned-owner recovery, ACL rollback, LocalService pipe health and package integrity; owner acceptance must separately exercise non-admin UI and explicit UAC setup.
