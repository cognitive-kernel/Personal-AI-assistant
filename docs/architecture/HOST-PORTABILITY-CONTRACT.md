# Host Portability Contract

## Goal

The application must run from different checkouts, installation paths, local accounts, service identities, hardware configurations, and supported operating systems without relying on values from a previous machine.

"Portable" does not mean avoiding every native operating-system API. File locking, process supervision, service installation, IPC, and filesystem permissions are OS facilities. Those facilities must be implemented in isolated platform adapters; shared application logic must not assume one OS or one machine identity.

## Non-negotiable rules

1. **No fixed local identity**
   - Never hardcode a human username, local account, domain, UID, GID, or machine-specific SID.
   - Discover the actual Runner/service identity from the installed service registration and resolve its numeric identity at provisioning time.
   - OS well-known service principals may be used only inside the corresponding platform security adapter; resolve them through OS APIs rather than scattering literal SIDs through shared logic.

2. **No user-profile dependency for shared runtime state**
   - Shared durable state must resolve to one machine-wide absolute path for all participating processes.
   - Do not derive active state, locks, checkpoints, or service configuration from Path.home(), USERPROFILE, LOCALAPPDATA, a login shell, or the repository checkout.
   - Per-user legacy state may be read only by an explicit, non-destructive migration path. Preserve both copies and stop on conflicts.

3. **No fixed installation or operating-system paths in shared logic**
   - Use platform path-discovery APIs such as platformdirs or native known-folder APIs.
   - Discover executables through trusted runtime metadata and PATH; validate ownership and permissions before using a machine service executable.
   - systemd, launchd, Windows Service Control Manager, Windows ACLs, and named pipes belong in platform adapters.

4. **No hardware assumptions**
   - Detect available CPU, memory, accelerators, and runtime capabilities dynamically.
   - GPU support is optional. Unknown hardware must remain a supported state; CPU-only execution must not fail because a vendor utility or accelerator is absent.
   - Do not select a device-specific binary or model solely from a fixed machine assumption.

5. **Core/platform boundary**
   - Shared application code consumes stable interfaces for paths, locks, process supervision, IPC, service health, and hardware capabilities.
   - Native implementations are selected at runtime and remain replaceable. Platform checks must not leak into unrelated business logic.

6. **Validation**
   - Run the test suite on Windows, Linux, and macOS.
   - Test with at least two distinct local identities and a service identity different from the interactive installer.
   - Test absent GPU/vendor tools, different CPU architectures where runners are available, protected state directories, and non-default installation roots.
   - Verify that a failed migration or provisioning attempt never deletes or overwrites existing state.

## Audit progress

The following portability corrections are in the current work branches:

- POSIX broker configuration now asks platformdirs for the machine configuration root instead of embedding Linux/macOS configuration paths in application code.
- POSIX broker Python discovery now uses runtime discovery and the system account database instead of fixed interpreter paths and assumed home-directory roots.
- POSIX state resolution no longer interprets the Windows-only PROGRAMDATA variable as a POSIX path.
- A separate broker ACL repair change addresses an elevated installer being unable to enumerate a service-protected state root, while retaining reparse-point rejection and rollback.

## Known remaining audit items

These are not considered solved until code and tests prove them:

- run.py Ollama discovery still contains per-user LOCALAPPDATA and home-directory candidates.
- Runtime handling of process-scoped AUTONOMOUS_ENGINEERING_PROGRESS_DIR must be reconciled with the machine-wide broker configuration so one login environment cannot split durable state.
- Windows built-in service and IPC principals are still represented by literal well-known SIDs in some adapter code; consolidate their resolution in the Windows adapter.
- Several modules still contain direct os.name or sys.platform branches for locks, process control, and hardware discovery; continue moving these behind explicit adapters.
- The current CI performs full-suite coverage on Windows and Linux, but macOS needs equivalent full-suite and broker coverage rather than bootstrap-only coverage.
- Hardware/model selection needs a complete audit to prove that resource limits and missing accelerators are handled through capability detection rather than assumptions.

## Completion criteria

The portability goal is complete only when every known remaining item is closed, the tests above pass on the supported platform matrix, and a repository-wide scan finds no fixed local account, home path, install path, machine identifier, or hardware requirement outside a documented platform adapter or explicit external-provider credential boundary.
