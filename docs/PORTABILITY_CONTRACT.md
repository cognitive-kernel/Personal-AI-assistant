# Portability and Environment Independence Contract

## Goal

The application should survive ordinary changes to machine identity, account names, group membership, installation directory, drive layout, OS patch level, CPU architecture, and optional developer tooling. Absolute independence from an operating system or hardware is impossible for privileged service installation; platform-specific operations must be isolated behind small, capability-detected adapters rather than leaking into application logic.

## Local-first is not offline-only

Local-first describes the preferred execution and data path, not a promise that every feature works without a network.

- Prefer usable local services, installed tools, local models, caches, and local data before reaching external services.
- Use the network when a selected capability intrinsically needs it or when a required resource is not available locally and the user-approved setup/fallback policy permits retrieval.
- Web research, ChatGPT-driven learning, remote GitHub operations, and initial retrieval of missing runtimes/packages/models may therefore require internet, an account, credentials, or a remote service. These are explicit dependencies of those capabilities, not dependencies of the entire application.
- A missing network connection, account, credential, or optional integration must disable or pause only the affected capability. Core startup, local storage, local task processing, and unrelated local modules must remain usable.
- Never silently upload local data merely because a remote service is available. Each module must define what it sends, why it needs the connection, and which consent/configuration gates apply.
- A remote dependency that is truly intrinsic to a feature may be mandatory when that feature is enabled; it must not become an unconditional prerequisite for installing or running the whole application.


## Non-negotiable design rules

1. **No identity-name assumptions.** Resolve the active account and service principals at runtime. Persist stable Windows SIDs or POSIX numeric UID/GID values where authorization requires identity; never assume a username, group name, domain, or machine name.
2. **No fixed installation paths.** Derive the application root from the installed executable/package location. Resolve shared state and logs through platform known-folder APIs or standard OS directory services. Support an explicit absolute override, validate it, and never silently fall back to a per-user directory for machine-wide state.
3. **No shell-version coupling.** Core installation, service control, ACL changes, path discovery, and health checks must not require Windows PowerShell, PowerShell 7, .NET Framework compiler tools, a particular shell profile, or inherited module paths. During transition, any remaining PowerShell launcher must sanitize inherited module discovery before spawning an elevated child and restore the parent environment afterward.
4. **No runtime compilation on the target host.** Build native/service artifacts in CI for declared OS/architecture targets. Installation must not require csc.exe, a local SDK, compiler, or developer toolchain.
5. **Capability detection over version guessing.** Probe the exact API/service-manager/filesystem capability needed. Use OS version and architecture only to select a tested adapter/package, not to infer that a feature exists.
6. **Dependencies are scoped to capabilities.** Discover local and remote integrations independently. A dependency may be required by the specific feature that uses it (for example, ChatGPT access for ChatGPT-based learning or GitHub access for remote PR operations), but it must not become a global startup requirement. Missing dependencies pause or disable only the affected capability; unrelated local features remain available.
7. **Relocatable and Unicode-safe.** Test paths containing spaces, non-ASCII characters, long paths, different drive letters, and install locations outside default folders. Quote process arguments structurally; do not build unsafe command strings.
8. **Safe upgrades and migrations.** Preserve user data and existing configuration. Use additive, versioned migrations, backups/rollback for replaced binaries, atomic writes, and explicit preflight. Never delete or overwrite existing state as an installation side effect.
9. **Least privilege by resolved identity.** Do not grant access to broad groups as a portability shortcut. Build and verify ACLs using resolved SIDs/UIDs and preserve unrelated data outside the managed state root.
10. **Explicit support matrix.** Publish tested OS families/versions and CPU architectures. Unknown combinations must fail with a clear diagnostic before mutation, not proceed on optimistic assumptions.

## Target architecture

- Keep the application and domain logic in the existing Python package.
- Define one versioned, platform-neutral broker protocol and shared conformance tests.
- Implement privileged broker provisioning behind platform adapters:
  - Windows: native Win32 service-control and security-descriptor APIs; no PowerShell module imports and no .NET Framework compiler at install time.
  - Linux: systemd adapter when available, with a documented non-service fallback only where security requirements permit.
  - macOS: launchd adapter when available, with the same protocol and fail-closed security checks.
- Build and sign/package platform-specific broker binaries in CI for each supported OS/architecture. Do not compile on user machines.
- Resolve app/state/log/cache/config paths through OS APIs and support relocation without relying on the current working directory or a particular user profile.
- At startup, run a read-only capability preflight and report which optional capabilities are available before requesting privileged changes.

## Migration plan

### Stage 1 — stop environment contamination (current patch)

Before creating an elevated child, restrict its module search path to the active PowerShell host's own inbox modules. Restore the parent process environment in finally. Add regression coverage that asserts the environment is sanitized before process creation. This is a containment fix only; it does not complete the shell-independence goal.

### Stage 2 — remove Windows PowerShell and runtime compiler dependencies

Move Windows broker installation, service registration, identity resolution, ACL management, and health verification into a prebuilt native Windows helper. Replace PowerShell-specific behavior with explicit Win32 calls. Keep the Python installer as the orchestrator and invoke the helper with a structured, versioned request/response contract.

### Stage 3 — package and relocate

Produce signed self-contained artifacts for each supported OS/architecture in CI. Add installation-root discovery, standard-directory resolution, upgrade/rollback, and relocatability tests. Remove target-host compiler and SDK requirements.

### Stage 4 — compatibility and migration matrix

Run conformance tests across supported OS versions, x64/ARM64 where applicable, standard and nonstandard usernames, local/domain identities, non-ASCII and relocated paths, different service managers, and absent optional integrations. Add upgrade tests from existing installations and preserve existing state.

## Acceptance criteria

- A clean target machine does not need PowerShell, .NET Framework SDK/compiler, Visual Studio, or a developer toolchain to install the Windows broker.
- Changing the Windows account name, domain, local group membership, machine name, install drive, or install directory does not require source edits or hard-coded configuration.
- PowerShell 5.1 and 7, if present, are irrelevant to core installation.
- Supported Linux/macOS installations use the same broker protocol and pass the same conformance suite.
- Missing Runner/Ollama/Git/browser/GPU capability does not prevent unrelated core features from starting.
- An unsupported OS/architecture or missing required security primitive is detected before services, ACLs, or existing state are changed.
- Upgrade tests prove that existing state and configuration are retained and rollback is possible.