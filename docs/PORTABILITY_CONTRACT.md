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
3. **No single-shell or exact-version lock-in.** The application must not require one specific shell/version (for example, PowerShell 7 only) when a compatible implementation exists through another supported runtime or a bundled/native helper. Prefer the application's own implementation for core behavior. When a system tool is useful, discover its actual executable, version, architecture and required capabilities, then select a compatible adapter/script. Do not assume `pwsh` and Windows PowerShell are interchangeable; keep version-specific paths isolated and tested. Never depend on an interactive shell profile or inherited module paths.
4. **No target-host developer toolchain requirement.** Prefer bundled, prebuilt and integrity-verified components for functionality owned by the application. Do not require csc.exe, a local SDK, compiler, IDE or developer toolchain merely to install/run. Use OS-provided tools when they are genuinely appropriate and available, but discover and validate them instead of assuming their presence.
5. **Capability detection plus version-aware adapters.** Probe the exact API/service-manager/filesystem/runtime capability needed. Use versions and architecture to select a tested implementation when APIs differ, then verify the capability at runtime. Do not reject a newer/older runtime solely because its version string differs when a tested compatible path exists.
6. **Dependencies are scoped to capabilities.** Discover local and remote integrations independently. A dependency may be required by the specific feature that uses it (for example, ChatGPT access for ChatGPT-based learning or GitHub access for remote PR operations), but it must not become a global startup requirement. Missing dependencies pause or disable only the affected capability; unrelated local features remain available.
7. **Relocatable and Unicode-safe.** Test paths containing spaces, non-ASCII characters, long paths, different drive letters, and install locations outside default folders. Quote process arguments structurally; do not build unsafe command strings.
8. **Safe upgrades and migrations.** Preserve user data and existing configuration. Use additive, versioned migrations, backups/rollback for replaced binaries, atomic writes, and explicit preflight. Never delete or overwrite existing state as an installation side effect.
9. **Least privilege by resolved identity.** Do not grant access to broad groups as a portability shortcut. Build and verify ACLs using resolved SIDs/UIDs and preserve unrelated data outside the managed state root.
10. **Explicit support matrix.** Publish tested OS families/versions and CPU architectures. Unknown combinations must fail with a clear diagnostic before mutation, not proceed on optimistic assumptions.

## Adaptive runtime and compatibility policy

Use this preference order for each capability, without turning a feature-specific prerequisite into a global application prerequisite:

1. **Application-owned implementation:** use code and tools shipped with the application whenever they can perform the task safely and reliably.
2. **Compatible local runtime/tool:** if an operating-system tool is the right implementation, discover installed candidates and inspect their real version, architecture and capabilities. Select a compatible path instead of requiring one exact release.
3. **Alternate adapter:** when behavior differs between versions or platforms, use a tested adapter or script for that runtime. Keep version-specific code behind a stable internal interface.
4. **Approved acquisition:** only when the selected feature needs a missing component, and no supported local path exists, offer or perform a verified download according to that feature's configuration and consent. Cache the component locally for later runs.
5. **Scoped degradation:** if no supported path is available, mark only that capability unavailable and explain the exact missing requirement. Keep the application core and unrelated modules running.

Examples and requirements:

- **PowerShell:** detect Windows PowerShell (`powershell.exe`, commonly 5.1) and PowerShell (`pwsh.exe`, 7.x) separately. Query `$PSVersionTable` and required commands/modules before choosing an implementation. Use a common implementation only when its APIs are genuinely compatible; otherwise choose a tested version-specific adapter. If PowerShell is absent or unsuitable, use the bundled/native implementation where available. Never make one PowerShell major version a prerequisite for unrelated application features.
- **Python and packages:** ship or provision a verified compatible runtime and dependency set as part of the application installation workflow. Do not rely on whichever Python happens to be first on PATH. Support declared runtime ranges where tests prove compatibility; pin exact versions only for reproducible build inputs or a demonstrated compatibility/security requirement, not as an arbitrary user-machine restriction.
- **Operating system and CPU:** prefer portable application code. Where native components or wheels differ, detect OS/ABI/CPU architecture and select a matching prebuilt artifact. A 32-bit/64-bit or x64/ARM distinction is handled by packaging/adapter selection, not by hard-coded assumptions about the user's machine. Publish only combinations actually tested.
- **Fonts and UI resources:** include application-owned resources where licensing permits and provide system-font fallbacks. A particular optional font must not prevent startup.
- **Identity and privilege:** resolve the current identity and required service identity dynamically. Do not bind normal execution to one username, domain, group or Administrator account. Elevation is requested only for the specific operation that truly requires machine-wide privileges.
- **Paths:** derive executable and resource paths from the installed application location; resolve data/cache/log/config locations through OS facilities or explicit configuration. Do not assume the working directory, user profile, drive letter or installation folder.
- **Tooling:** optional tools such as Git, browser automation, Docker, Runner, GPU runtimes and model servers are feature capabilities. Discover them independently and do not block unrelated modules when they are absent.

A version check alone is not enough: select by the capabilities actually used, test the chosen adapter, and report the selected implementation in diagnostics. Do not silently fall back to a less secure implementation.

## Target architecture

- Keep the application and domain logic in the existing Python package.
- Define one versioned, platform-neutral broker protocol and shared conformance tests.
- Implement privileged broker provisioning behind platform adapters:
  - Windows: prefer the bundled, prebuilt broker/native Win32 service-control and security-descriptor implementation. A PowerShell adapter may be used where it is the best supported path, but it must detect 5.1/7 capabilities and select compatible code; neither version is a global prerequisite. Do not compile on the target host.
  - Linux: use systemd when available, with a documented non-service fallback only where security requirements permit; detect the actual service manager and capabilities.
  - macOS: use launchd when available, with the same protocol and fail-closed security checks.
- Build and integrity-check/package platform-specific broker binaries in CI for each supported OS/architecture. Use the matching artifact at install time; never require a compiler on user machines.
- Resolve app/state/log/cache/config paths through OS APIs and support relocation without relying on the current working directory or a particular user profile.
- At startup, run a read-only capability preflight and report which optional capabilities are available before requesting privileged changes.

## Migration plan

### Stage 1 — stop environment contamination (current patch)

Before creating an elevated child, restrict its module search path to the active PowerShell host's own inbox modules. Restore the parent process environment in finally. Add regression coverage that asserts the environment is sanitized before process creation. This is a containment fix only; it does not complete the shell-independence goal.

### Stage 2 — remove fixed shell/compiler assumptions

Move security-sensitive broker installation, service registration, identity resolution, ACL management, and health verification into the bundled prebuilt native helper where that is the most reliable implementation. Keep the Python installer as orchestrator and use a structured, versioned request/response contract. Retain PowerShell only where it provides a useful supported OS integration; discover 5.1/7 separately and select the tested adapter for the detected runtime. Neither a fixed PowerShell version nor a compiler on the target host is allowed to become a core prerequisite.

### Stage 3 — package and relocate

Produce signed self-contained artifacts for each supported OS/architecture in CI. Add installation-root discovery, standard-directory resolution, upgrade/rollback, and relocatability tests. Remove target-host compiler and SDK requirements.

### Stage 4 — compatibility and migration matrix

Run conformance tests across supported OS versions, x64/ARM64 where applicable, standard and nonstandard usernames, local/domain identities, non-ASCII and relocated paths, different service managers, and absent optional integrations. Add upgrade tests from existing installations and preserve existing state.

## Acceptance criteria

- A clean target machine does not need a particular PowerShell major version, .NET Framework SDK/compiler, Visual Studio, or a developer toolchain to install/run the core application.
- If PowerShell is used, the installer detects the installed edition/version and selects a tested compatible adapter; PowerShell 5.1-only, PowerShell 7-only and no-PowerShell environments are covered wherever a bundled/native path is available.
- Changing the Windows account name, domain, local group membership, machine name, install drive, or install directory does not require source edits or hard-coded configuration.
- Supported runtime versions are expressed as tested compatibility ranges; exact-version pins are reserved for build reproducibility or proven technical/security constraints.
- Supported Linux/macOS installations use the same broker protocol and pass the same conformance suite.
- Missing Runner/Ollama/Git/browser/GPU capability does not prevent unrelated core features from starting.
- An unsupported OS/architecture or missing required security primitive is detected before services, ACLs, or existing state are changed.
- Upgrade tests prove that existing state and configuration are retained and rollback is possible.

## Implementation status — 2026-11-23

- **Stage 1 — code implemented in draft PR #863, exact-head CI pending:** active-host discovery, PowerShell Core/Desktop capability probing without a numeric version floor, active-host UAC relaunch, module-path isolation, and limited-mode dashboard behavior.
- **Stage 2 — partial in draft PR #863:** the target host no longer compiles the Broker; CI builds a prebuilt .NET Framework service executable. Package/hash/runtime/service-manager checks run before Runner stop or ACL changes. A CI job commits the verified package to the PR branch; a subsequent exact-head run must compare a fresh deterministic build with that committed executable. The installer still uses a compatible PowerShell host and requires the .NET Framework 4.8+ runtime. A native/PowerShell-free installer and service-control/ACL implementation remain outstanding.
- **Stage 3 — partial:** SDK `11.0.100` and reference package `Microsoft.NETFramework.ReferenceAssemblies.net48 1.0.3` are pinned for reproducible CI only. CI emits a SHA-256 manifest and GitHub build-provenance attestation. The target installer verifies SHA-256 but does not independently verify the GitHub attestation. Cross-platform/architecture build matrix remains incomplete.
- **Stage 4 — pending:** OS/architecture, identity, relocated path, upgrade, migration, and rollback conformance matrix has not been completed.
- **Additional regression fix:** the installer now preflights the package, its hash, .NET runtime capability, and `sc.exe` before stopping Runner services or modifying state ACLs. The dashboard surfaces the bounded Broker diagnostic instead of hiding it behind a generic status.

This status is not a declaration that the full portability contract is complete. Merge remains gated on exact-head CI, committed-package verification, and target-host acceptance.
