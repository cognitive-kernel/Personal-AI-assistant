# Autonomous local Ollama engineering

## Purpose

This workflow replaces the paid OpenAI API dependency with the local Codex CLI connected to Ollama. A Windows self-hosted GitHub Actions runner on the target computer executes one bounded coding task at a time. Each task gets its own non-draft pull request. GitHub-hosted tests, browser E2E, and dependency-audit workflows validate that exact commit. After exact head/base verification and successful checks, the PR remains open for owner review. The controller never merges PRs automatically. After the owner manually merges a validated PR, the controller verifies the exact merge commit and all post-merge checks before clearing the durable task and dispatching the next run from refreshed `main` in continuous mode.

The worker is event-driven and periodic. It is not a model process generating code continuously for 24 hours. Each Codex task attempt is capped at 30 minutes, and Codex inference/recovery is capped at two attempts and 45 minutes of actual Codex runtime. Local tests and exact-head GitHub validation use a separate 90-minute total task lifecycle budget, so verification time does not consume the inference budget. A missing/invalid completion marker triggers one bounded report-finalization retry of the same task: the controller first instructs Codex to inspect the existing plan, diff, and report and avoid repeating completed implementation or broad tests. The existing worktree and checkpoint are preserved. These are task-execution bounds, not a guarantee that setup, tests, and GitHub validation all finish within 45 minutes.

## Live Pause/Mode credential

The post-concurrency workflow gate reads the current repository Actions variables through the REST API. The workflow's `GITHUB_TOKEN` cannot be granted the repository `Variables: read` permission, and a newly provisioned non-administrator Runner account is not assumed to inherit the installer's GitHub CLI login.

Before enabling autonomous execution, create a fine-grained token restricted to this repository with **Variables: read** and save it as the repository Actions secret `AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN` (GitHub repository → Settings → Secrets and variables → Actions). The post-concurrency PowerShell gate, the Python cycle controller, and the Python recovery coordinator use this token only for read-only repository identity and Pause/Mode variable GET requests. All other workflow/controller operations continue to use the separate `GITHUB_TOKEN`. The secret-bearing cycle job is restricted to `refs/heads/main`; the recovery workflow accepts only failed runs whose head branch is `main`. The cycle controller removes the secret from its process environment before launching Codex, so the model subprocess does not inherit it.

If the secret is missing, cannot read variables, or returns malformed settings, the live gate fails closed and starts no task. Never put the token in source files, workflow inputs, issues, logs, or chat.

## No paid API key

- No `OPENAI_API_KEY` secret is used or required.
- Local inference uses Ollama at `http://127.0.0.1:11434`; local Ollama requests do not require an API key.
- A ChatGPT subscription is not used by this workflow.
- The GitHub Actions minutes used by the GitHub-hosted validation/publish jobs remain subject to GitHub account limits.

Official references: [Ollama Codex integration](https://docs.ollama.com/integrations/codex), [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode), [Ollama local API](https://docs.ollama.com/api/introduction).

## Target hardware and model

The repository's target machine is documented as:

- 32 GB RAM
- Intel Core i5-12400 (12th Gen)
- Intel integrated graphics with 128 MB reported graphics memory

There is no default or fallback model. The operator must explicitly select an installed model in the dashboard and save it before the autonomous workflow can run. The selected model must expose a positive architecture-specific `context_length` and enough KV-cache metadata to calculate a safe allocation from the current hardware. Tool support is accepted from Ollama's model capabilities metadata or a real local tool-call probe (`scripts/verify_ollama_tools.py`) when that metadata is absent. No fixed minimum or application-level maximum token count is imposed: the selected context is bounded by the model's own maximum and the current safe memory budget. This machine runs inference primarily on CPU, so coding sessions may be slow.

The autonomous-engineering status API preserves the installed-model inventory from local Ollama `/api/tags`; the selector offers only original base models and excludes internal `-codex-ctx` variants, including legacy numeric-suffix variants. It does not inject a fake default. The selected model is stored as repository Actions variable `AUTONOMOUS_ENGINEERING_MODEL`. Saving is allowed only when that exact model is installed, its architecture-specific context metadata and current hardware-safe context can be verified, and tool calling is confirmed from model capabilities or a cached live probe. The runner repeats model metadata and tool-capability checks before creating a context variant. If another model is selected, pull it on the Windows host first (for example, `ollama pull qwen3:14b`). If no model has been explicitly saved, readiness remains blocked and the workflow refuses to start; no model is selected or substituted automatically. The page also accepts an optional direct engineering task (maximum 2,000 characters) and forwards it as a `workflow_dispatch` input. For a non-empty manual dispatch targeting `main`, the serialized Windows `codex` job checks out trusted `main` without persisting Git credentials, then writes the task to `%ProgramData%\PersonalAIAssistant\\autonomous-engineering\\pending-manual-task.txt` only after it acquires the GitHub Actions concurrency slot. PowerShell persistence and Python controller operations share the same cross-session byte-range file lock; the controller records a pending PR before dispatching long-running CI, and clears the PR/task records together only after exact post-merge validation. The single task slot rejects a new submission while another autonomous run is queued or active; it is not a multi-item queue. Python and PowerShell serialize durable task/PR state through the same cross-session byte-range lock file under the shared state directory; the operating system releases ownership when a process exits. The workflow capture step only reads the locked persisted task and never rewrites it. Codex receives the task later as untrusted data; it cannot override the governing engineering/security prompt. The task text is part of GitHub's workflow-dispatch event metadata, so never include passwords, API keys, tokens, private user data, or other secrets.

## One-time Windows setup

1. Install Ollama for Windows and keep its server available at `http://127.0.0.1:11434`. The standard Windows installer is per-user by default; the autonomous Runner does not need the Ollama CLI in its own PATH and uses the loopback API. If the server normally runs only in an interactive tray session, configure a persistent service/startup method before relying on unattended runs.
2. Install the model you intend to use before selecting it in the dashboard:

   ```powershell
   ollama pull <selected-model>
   ```

   The workflow reads the selected model's architecture-specific maximum context, installed weight size, KV-cache dimensions, and currently available physical RAM. `app/ollama_context.py` selects the exact minimum of the requested context, model maximum, and safe hardware capacity; it has no fixed token floor or application-level token ceiling. Its memory budget reserves a percentage of currently available RAM and accounts for model weights plus a proportional runtime overhead; KV cache is budgeted at a conservative FP32 upper bound. When the selected base model or one of its generated context variants is already loaded, the preflight also reads `/api/ps` and accounts for its measured resident context instead of subtracting the model weights twice. Missing/invalid model metadata, missing RAM telemetry, or insufficient headroom stops the workflow rather than guessing a context. The selected value is persisted in `AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH`. The workflow creates a persistent model variant named `<selected-model>-codex-ctx` (one stable tag per selected base model; `num_ctx` is updated in place when the safe allocation changes) through Ollama `/api/create` with `num_ctx`, verifies the saved value through `/api/show`, loads it without a request-level `options.num_ctx` override, and verifies actual allocation through `/api/ps`. Codex receives that exact variant and context value in its model catalog. The original selected model is not modified, no Ollama server restart or administrator credential is required, and the workflow repeats runtime allocation verification after Codex execution. During every normal preflight (not `-VerifyOnly`), `scripts/ollama_legacy_cleanup.py` audits numeric-suffix Codex aliases across every local base-model family, not just the currently selected model. A tag is eligible only when its name ends in `-codex-ctxN` and `/api/show` confirms a positive persisted `num_ctx` exactly equal to `N`. Aliases currently loaded according to `/api/ps` are preserved and retried on a later preflight. Base tags, the stable `-codex-ctx` tag, and `-codex-64k` tags do not match the deletion pattern. After each DELETE request, the helper re-reads `/api/tags` and fails the preflight if the alias is still listed. To inspect the cleanup decision without deleting anything, run `python scripts/ollama_legacy_cleanup.py --current-model <current-model> --audit-only` from the repository root. The variant shares the base model weights; it is a separate model configuration, not a second model download. The RAM estimate is a preflight estimate; successful load, `/api/ps`, and a real tool-call smoke test are runtime acceptance evidence. See the [official context-length guide](https://docs.ollama.com/context-length), [Modelfile reference](https://docs.ollama.com/modelfile), and [API reference](https://github.com/ollama/ollama/blob/main/docs/api.md). A larger context increases memory use; measure stability on the target host.


3. Install Node.js LTS system-wide so the executable is available to the Windows Runner service.
4. Install GitHub CLI and authorize it once with the **same GitHub account already signed in to Chrome**. Use `gh auth login --web`; GitHub opens its browser authorization flow and the existing Chrome session can be used. This does not create another GitHub account or require a paid service. The authorization is stored locally for the application account and is reused by the dashboard; do not authenticate GitHub CLI as the Runner service account:

   ```powershell
   gh auth login --web
   gh auth status
   ```

5. From an elevated PowerShell window in the repository checkout, provision the Runner and machine state broker in one operation:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\\scripts\\provision_autonomous_runner.ps1 -Repository "cognitive-kernel/Personal-AI-assistant"
   ```

   For an existing installation, the script discovers the registered Runner service, reads its actual `StartName`, and derives its installation root from `RunnerService.exe`; `-RunnerAccount`, `-RunnerName`, and `-RunnerRoot` are optional overrides, not required fixed values. If no Runner exists, it generates a machine/repository-scoped non-admin local service-account name and uses the operating system's shared application-data root rather than a hard-coded drive path. If more than one Runner installation is detected, it stops and asks for an explicit `-RunnerRoot` rather than selecting one arbitrarily.

   The provisioning step compiles and installs `PersonalAIAssistantStateBroker` as a LocalService Windows service under the machine-wide `%ProgramData%\\PersonalAIAssistant\\StateBroker` directory. The state directory ACL is reset recursively; only the discovered Runner SID and LocalService retain direct Modify access. Any authenticated local Windows session can use the fixed, allow-listed named-pipe operations; no application-account ACL, fixed user name, or general Users/Everyone data-directory grant is required. Remote named-pipe clients are rejected. The script verifies the selected Ollama model, obtains a short-lived registration token through the authenticated GitHub CLI, downloads the official Windows x64 Runner when needed, verifies GitHub’s published SHA-256 digest, and assigns the unique `autonomous-state-owner` label. Re-running it preserves an already registered Runner and does not re-register it. The Runner configuration tool prompts for any new service-account password; passwords and registration tokens are not written to files or printed. A custom state root may be supplied through the machine-scoped `AUTONOMOUS_ENGINEERING_PROGRESS_DIR`; process-only or per-user overrides are rejected.

   On Windows, `run.py` checks the broker before starting the dashboard. If the broker is missing or unhealthy, it invokes the dedicated idempotent setup script and requests administrator approval through UAC. **The broker is a core dashboard prerequisite and does not require a GitHub Runner to exist.** With no Runner service installed, setup still installs the LocalService broker and grants the shared state root only to LocalService; the local UI can open and report readiness, while autonomous dispatch remains unavailable until the separate Runner capability is provisioned. When Runner services exist, their identities are discovered from the Windows service `StartName`; the installer does not read the protected `.runner` registration file. It stops all discovered Runner listeners before shared ACL changes, refuses to proceed while a worker is active, and restarts every stopped listener. Unresolvable service paths fail closed before ACL changes. It does not change state ACLs while a `Runner.Worker.exe` job is active. If UAC is declined, broker installation fails, or an active worker prevents safe repair, the application still starts in limited mode and reports that shared progress is unavailable; it does not broaden permissions or delete state. If Ollama installation, availability, or required-model preparation fails, the local UI also starts in limited mode and reports readiness as unavailable instead of exiting before the dashboard can be opened. `--check-only` still returns a non-zero status when a core runtime prerequisite is unavailable; Python dependency installation failures remain fatal because the application cannot import its runtime.
   The separate Runner-provisioning command requires an elevated shell and repository **Administration: write** permission. It refuses to overwrite an existing configured Runner or a non-empty unconfigured Runner directory. If a Runner is already configured, it verifies the service identity and starts it without re-registering.

6. In **Settings → Actions → General → Workflow permissions**, allow GitHub Actions to create and approve pull requests if the repository requires it. The autonomous job requests only `contents: write`, `pull-requests: write`, and `actions: write` for the trusted controller. The controller never calls GitHub's PR merge API. It leaves the validated PR open for owner review; the owner performs the merge manually. Do not disable branch protection or grant a bypass.
7. From any Windows account that will launch the application, run the app-side readiness check:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\\scripts\\check_autonomous_agent.ps1
   ```

   This checks the local Ollama endpoint/model, that account's GitHub CLI authentication, Runner.Listener visibility, and the machine broker's storage health. No app-account filesystem ACL or fixed application username is required. Pass the exact saved model via `-SelectedModel <saved-model>`. It intentionally does not require Codex CLI to be installed in the application session.
8. From the Runner service account, run the Runner-side check from its checked-out workspace:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\\scripts\\check_autonomous_runner.ps1
   ```

   This checks that the Runner account is non-administrator, plus Codex CLI, the Ollama loopback API and selected model, Runner.Listener, the Runner's direct write access to the state root, and the LocalService broker's health. Ollama CLI installation and PATH visibility are not Runner prerequisites. Pass the exact saved model via `-SelectedModel <saved-model>`. It does not require or use GitHub CLI authentication. The workflow repeats the critical Runner checks before every Codex session.
9. Start the local app and open `http://127.0.0.1:8000/autonomous-engineering`. When application-side readiness passes, use **شروع صف پیوسته** or start the workflow from GitHub Actions. The workflow performs its own Runner-side checks and fails closed if the Runner account is not configured correctly.

## Workflow behavior

A local Codex coding attempt has a 60-minute hard process-tree deadline. This is intentionally longer than the 30-minute default because the documented target host runs local inference primarily on CPU; the task controller still limits a task to two attempts and a two-hour total task budget, preserves checkpoints between attempts, and stops before the six-hour workflow deadline. The short sandbox smoke probe remains separately bounded and is not lengthened.

- GitHub checks the schedule hourly at minute 17 UTC. Scheduled mode accepts exactly 1, 2, 4, 6, 12, or 24 hours between runs. Continuous mode is the default: a main-branch update or hourly heartbeat starts work automatically when no other run is active; after each owner-verified merge and successful post-merge CI, the controller dispatches the next bounded run immediately. Manual task persistence occurs inside the serialized `codex` job only after the schedule gate and concurrency slot are acquired. A paused or already-active/queued dispatch is rejected without accepting or persisting its task; accepted tasks are then durable across retries and recovery. The workflow has no workflow-level concurrency group that could replace an unpersisted dispatch; the Codex job is serialized separately, while the active-run guard and repository execution lock prevent overlapping work. Duplicate scheduled/manual/recovery triggers are suppressed by checking active workflow runs. It is not limited by a daily run count.
- The selected interval is stored in `AUTONOMOUS_ENGINEERING_INTERVAL_HOURS` and accepts exactly 1, 2, 4, 6, 12, or 24 hours. The model and interval are saved using the existing GitHub CLI authorization on the Windows host.
- The execution mode is stored in `AUTONOMOUS_ENGINEERING_MODE` (`scheduled` or `continuous`). Scheduled mode checks the hourly cron against the configured interval. Continuous mode starts on a main-branch push or the hourly heartbeat. Each run performs one bounded task, runs the full Windows test suite and repository quality gates, creates a PR, dispatches `tests.yml`, `browser-e2e.yml`, and `dependency-audit.yml` against the exact candidate SHA, verifies the unchanged PR head and `main` base, and leaves the PR open for owner review. After a manual merge, the next run verifies the merge commit and dispatches the three required workflows against that exact post-merge SHA before clearing the durable task and continuing. The first session may consume the submitted manual task. An open autonomous PR blocks duplicate task generation.
- Each workflow run is bounded by GitHub's six-hour job limit and publishes at most one PR. The overall queue is not limited to eight tasks or one six-hour run: continuous mode dispatches a new run after each verified merge. A failed or unverified task is never merged or counted as accepted. The queue stops when paused, empty, blocked, or when verification cannot be repaired. No deployment is performed.
- The Codex job runs only on the uniquely state-owning runner carrying `self-hosted`, `Windows`, `X64`, `ollama`, and `autonomous-state-owner` labels and only when the ref is `main`.
- The local job checks that Ollama, the configured model, and Codex CLI are present before starting.
- After each Codex task, the Windows self-hosted runner executes the full default pytest suite and the repository artifact, Markdown-link, import-cycle, retrieval, and claim-to-test quality gates against that task's working tree. Before owner review, the three GitHub-hosted workflows independently test the exact PR head, including the browser E2E suite and dependency audit.
- A read-only GitHub API snapshot is created before Codex runs. The workflow token is not passed to the Codex process.
- Codex is invoked with the custom local provider ID `ollama-local`, a local Ollama `/v1` Responses endpoint, and the `workspace-write` sandbox. The ID is intentionally distinct from Codex's reserved built-in provider IDs; the workflow does not redefine `[model_providers.ollama]`. Its proposed patch is restricted to `app/`, `tests/`, and `docs/` (including `scripts/`).
- The task controller creates one isolated PR per verified task. It explicitly dispatches the repository's Linux test, browser E2E, and dependency-audit workflows on the PR branch and verifies each completed run's branch and head SHA before handing the PR to the owner for review.
- The workflow token is removed from the Codex and test subprocess environments. A trusted publisher subprocess receives it only after local verification, pushes the task branch, creates/reuses a non-draft PR, dispatches exact-commit checks, revalidates the PR head/base, and leaves the PR open. It never calls the PR merge API; after an owner merge, the controller runs post-merge checks on the exact merge SHA.
- The agent never pushes directly to `main` or deploys. The trusted controller never merges PRs; it leaves each validated PR open for owner review. After the owner manually merges, the controller verifies the exact merge SHA and post-merge checks. It cannot change secrets/security settings or modify real databases.

## Dashboard

The local page at `/autonomous-engineering` reports Ollama reachability, selected-model presence, the Codex CLI visible to the current application session (informational only), Runner.Listener process state, current-session GitHub CLI authentication, brokered shared progress-storage access, and recent workflow runs. The workflow separately verifies Codex CLI and direct write access under the actual Runner service account before every coding session. It provides explicit manual dispatch and cancellation controls. It does not install the Runner or silently start privileged Windows services.

During each Codex session, a detached Windows checkpoint watcher atomically saves the allowlisted worktree diff and agent plan to `%ProgramData%\PersonalAIAssistant\\autonomous-engineering` every 20 seconds. Before the next checkout cleanup, the workflow stops the previous watcher and captures a final snapshot. A retry restores the checkpoint only when its immutable base SHA still matches `main`; otherwise it preserves the checkpoint and fails closed instead of applying it to a different code base. Watcher ownership metadata binds run ID, task, base SHA, PID, and process creation time. Recovery must confirm the exact process has exited before changing checkpoint state; access denied, ambiguous identity, or an unresponsive process aborts recovery and preserves the checkpoint. The watcher process lifecycle is isolated from checkpoint serialization policy in `scripts/autonomous_engineering_checkpoint_process.py`. Checkpoint files stay outside the Git repository and never contain workflow credentials.

The separate `autonomous-codex-recovery.yml` workflow retries eligible failed or timed-out continuous task executions from the last accepted `main` checkpoint. A submitted manual task is persisted in the Runner's durable state only after the serialized job acquires its concurrency slot; paused or duplicate dispatches are rejected before the task is accepted or persisted. A failure in the schedule gate or manual-task persistence step is an admission/gating failure, not a failed autonomous task, and is excluded from the execution-failure streak. The recovery policy classifies structured GitHub job/step results; unknown classifications fail closed without dispatch. The controller clears the durable task only after it confirms the corresponding PR merge. Each workflow run permits at most two Codex attempts within a two-hour task budget, and controller commands have independent deadlines with process-tree cleanup. Recovery uses bounded exponential backoff capped at 4 minutes and opens a circuit breaker after three consecutive controller task-execution failures; an open breaker is explicitly reported as a failed recovery job with a job summary, not a green success. The pending task and checkpoint are preserved for owner inspection. Recovery resumes only after the root cause is corrected and the owner explicitly resumes the queue. A retry does not count a failed task as complete.

The page provides durable pause/resume controls. The continuous queue starts automatically after a main-branch update or on the next hourly heartbeat, so no manual first dispatch is required. Pause sets `AUTONOMOUS_ENGINEERING_PAUSED=true`; the current task reaches its safe boundary, and no new task starts. Resume clears the flag and dispatches a new run from the latest persisted project state. Emergency cancel remains separate and terminates the active run.

The page also separates two progress measures:

- **Agent plan progress:** the percentage is the completed/total count of Codex's structured plan items (equal-weight task count, not estimated effort). The current workflow phase and active step are displayed separately; the plan can reach 100% before tests, independent validation, owner review, manual merge, and post-merge checks finish. Codex runs with `--json`; the wrapper records bounded activity events and never publishes raw command output or model reasoning. The plan file is temporary and excluded from the candidate patch.
- **Project progress:** the engineering-completion and overall-acceptance percentages from `docs/ACCEPTANCE_SCORECARD.md`. The workflow snapshots the trusted `main`-branch scorecard before Codex starts. A dated chart point is recorded only when a later run observes changed accepted percentages on `main`; unmerged candidate changes are never recorded, and repeated runs with unchanged scores do not create artificial chart points. This keeps daily, weekly, and custom date-range charts limited to verified progress changes. Historical points are not fabricated or backfilled.

The app polls the lightweight progress endpoint every five seconds and draws the chart with native SVG; no charting dependency or full-page reload is used. Activity details are collapsed by default. Progress state and its bounded event history are stored under `%ProgramData%\\PersonalAIAssistant\\autonomous-engineering`. The Runner SID and LocalService broker SID alone have direct filesystem access. App sessions use the local named-pipe broker for fixed read/status operations; they never receive read/write ACLs on the protected state tree. If the service or state root is unavailable, the page reports that live telemetry is unavailable rather than inventing values.

The AI-learning page and autonomous-engineering page both include the primary navigation menu. The main dashboard also links to the autonomous-engineering page.

## Security and operational limitations

- A self-hosted runner executes repository workflow code on the local host. Use a dedicated, non-administrator service account, separate from the local app user's GitHub CLI account; do not store a personal GitHub CLI login or unrelated credentials in the runner account. Do not register this machine for arbitrary repositories or untrusted PR workflows.
- The autonomous workflow has no `pull_request` trigger. Its coding job runs only for `push` to `main`, scheduled heartbeat, or an explicit `workflow_dispatch` targeting `main`; the manual-task persistence job is likewise restricted to non-empty manual dispatches targeting `main`. PR validation runs in separate GitHub-hosted workflows. Never add `pull_request_target` or run untrusted PR code on the self-hosted runner.
- Keep the runner's working directory and Windows account separate from personal documents, browser profiles, and unrelated credentials where practical.
- Keep the app bound to loopback. The dashboard controls are not authentication; local processes on the computer can still access the local API.
- A persistent runner can wait for jobs around the clock, but actual work occurs in finite workflow runs. The hourly schedule can be delayed, and the computer, network, Ollama server, and runner service must remain available.
- Local CPU inference can be much slower than hosted inference. The target-host performance/soak run, independent security review, and required repository acceptance gates remain mandatory; each task PR requires owner review and manual merge before the queue advances.
- The UI's readiness indicator is diagnostic only. A green state does not prove model quality, security, or successful workflow execution.

## Troubleshooting

- **Ollama offline:** start Ollama and verify `Invoke-RestMethod http://127.0.0.1:11434/api/tags`.
- **Model missing:** run `ollama pull <saved-model>`, then select and save that exact model in the dashboard.
- **Codex missing:** install with `npm install -g @openai/codex`, then restart the runner service so its PATH is refreshed.
- **Runner missing:** inspect the runner service in Windows Services and confirm the `ollama` and unique `autonomous-state-owner` labels.
- **Workflow queued:** confirm the runner is online and has all required labels.
- **Manual dispatch unavailable:** authenticate GitHub CLI for the same Windows account that runs the app and verify repository Actions permissions.
- **Model context or memory errors:** check Ollama's loaded model/context configuration and reduce concurrent workloads; do not claim successful operation until a real run completes.


## Automatic Codex CLI provisioning

The autonomous workflow installs `@openai/codex` automatically on its first run under the dedicated Runner service account. Node.js LTS and npm must be installed system-wide and available in the Runner service PATH. The workflow uses that account's npm global prefix and does not require an OpenAI login or API key because it explicitly selects the local Ollama provider. No GitHub CLI credentials are stored in the Runner account.

This automatic provisioning section supersedes the earlier step 5 instructions to sign in as the Runner account and install Codex CLI manually.


## Windows identity and path portability

The autonomous engineering components do not assume a particular Windows profile name,
Python installation directory, or Ollama installation directory. Workflow steps resolve
`python`, `node`, `npm`, and Git from the PATH visible to the actual Runner service identity.
Ollama is discovered through the shared API contract in `scripts/AutonomousOllamaRuntime.psm1`
at `http://127.0.0.1:11434`; the service account does not need access to the installing user's
Ollama executable or profile. The API server must remain available on loopback, and model,
capability, context, and runtime-allocation checks remain mandatory.

The Runner installation root defaults to
`%ProgramData%\\GitHubActionsRunner`. The shared state root defaults to
`%ProgramData%\\PersonalAIAssistant\\autonomous-engineering` and can be overridden
with `AUTONOMOUS_ENGINEERING_PROGRESS_DIR`, which must be an absolute path. Windows
fails closed rather than falling back to a per-user home directory if `PROGRAMDATA`
is unavailable. The provisioning script resolves the dedicated Runner account to its
current SID. It does not require or resolve an application account. A LocalService
Windows service owns the machine state broker and the direct filesystem ACL includes
only the Runner and LocalService SIDs. The dashboard communicates with the broker over
a local named pipe; authenticated local sessions can use only fixed allow-listed
operations and cannot provide paths, enumerate the data directory, or access
checkpoint files directly. The pipe rejects remote clients. The data directory is
never granted to the machine-wide Users group or Everyone.

Python and PowerShell serialize durable state through the same byte-range lock file
inside the shared state directory. The lock works across Windows logon sessions.
The LocalService broker uses that same lock for marker inspection and cleanup,
preserving cross-process exclusion. The lock is local to one host and is not a
distributed lock. Keep the Runner service non-administrator; do not loosen upstream
Codex sandbox ACLs or substitute unsandboxed execution.

### Dashboard admission and pending-work safety
The Runner and controller resolve durable storage through the shared
execution_state_directory() contract. On Windows, the dashboard uses the LocalService
state broker for health, marker inspection, progress telemetry, diagnostics, path-free
checkpoint status, and bounded cleanup. POSIX exposes the same application contract
through its restricted Unix-domain-socket broker and OS-wide machine data root. The
Runner and workflow continue to write to the same machine state root using the shared
cross-process locks. The dashboard checks durable task, task-owner, pending-PR,
interrupted-cleanup, and checkpoint state before accepting a **new non-empty manual
task**. Checkpoint status comes from an atomically published pointer; the dashboard
does not wait on the watcher's lifetime lock and receives only the run ID, task,
base SHA, timestamp, patch byte count, and verification state. It never receives task
text, patch contents, or filesystem paths. If state cannot be inspected or verified,
admission fails closed. It also checks GitHub for an open controller-owned PR before
submitting a new manual task.

A verified active checkpoint blocks a new non-empty task but keeps empty dispatch
available to resume that same task. A corrupt, stale, or mismatched pointer blocks
continuation and new admission until reconciled. Completed checkpoints are deactivated
atomically; retained snapshots are not deleted or silently reactivated. Broker cleanup
refuses to remove progress data while a checkpoint is active or unverified. The
workflow's own serialized persistence and capture checks remain authoritative and
are not bypassed by dashboard preflight.

## Runtime prerequisite bootstrap

The autonomous workflow provisions its job-local Python 3.11 and Node.js 22 runtimes with pinned actions/setup-python and actions/setup-node action commits before invoking either runtime. These tools are downloaded into the GitHub Actions job environment; the host does not need a preinstalled system Python or Node.js, and the workflow does not modify another user's global runtime installation. The Codex CLI remains pinned to the repository's required version and is installed into the Runner service account's npm prefix.

This bootstrap does not install or authenticate GitHub CLI, install/start Ollama, download the selected model, register the self-hosted Runner, or provision service-account permissions. Those are host/service prerequisites and must remain visible as separate UI preflight items; missing authentication or privileged service setup cannot be fabricated by a package installer. The current coding job is still Windows-specific and this change does not claim Linux/macOS workflow support.
### Download the selected Ollama model from the dashboard

When the Ollama service is already running on the local loopback endpoint, the dashboard's model settings provide an explicit “Download and install selected model” action. The user chooses the model identifier and confirms the download; the UI then polls a bounded status record and displays Ollama's progress. Only one model pull is allowed at a time. The request is sent directly to the configured loopback-only Ollama API without proxy use or redirect following; model identifiers are validated and are never passed to a shell.

This action installs a model into the existing Ollama service. It does not install the Ollama application/service itself, choose a model on the user's behalf, or bypass the hardware/context checks performed before saving settings.
### GitHub CLI in the autonomous Runner job

The workflow provisions the pinned GitHub CLI release into a run-scoped directory when the required version is not already available. It downloads the official Windows x64 archive and checksum manifest, verifies SHA-256 before extraction, and adds only that job-local executable directory to the current job PATH. This does not modify the host's global installation. The workflow token supplies job authentication; it does not create or persist a Runner-account interactive login.
### Shared state on POSIX systems

The POSIX default is the OS-wide application data directory returned by platformdirs.site_data_path(). An explicit AUTONOMOUS_ENGINEERING_PROGRESS_DIR override must be absolute and outside a per-user home directory, and all participating app/Runner processes must receive the same value. On first resolution, any legacy ~/.personal-ai-assistant/autonomous-engineering state is copied under the durable-state, repository-execution, and checkpoint-store locks. The original directory is retained; identical existing files are accepted, conflicting files stop migration without overwriting either copy, and symbolic links stop migration. The shared directory is provisioned for the discovered service identities; the resolver does not grant direct state-file access to arbitrary app users or broaden permissions automatically.
### POSIX checkpoint lifecycle

Durable checkpoint restore, watcher startup, stop/snapshot, and cleanup use the same
shared state-root and checkpoint-index contract on POSIX and Windows. POSIX watchers
are launched in a new session so they remain independent of the foreground controller;
Windows retains its detached process-group launch. Missing PROGRAMDATA is not a reason
to disable checkpoint recovery on Linux or macOS. The POSIX broker socket and shared
data-root ownership are provisioned from the discovered service identity; the
application uses its allow-listed socket API and does not need direct access to
checkpoint files. No human login name, group name, home directory, or fixed hardware
identifier is configured by the operator. Model context is selected from runtime
metadata and available memory/compute resources, not a hard-coded GPU/CPU model.
State-root resolution does not require a login home directory. The current home is consulted only to discover a legacy per-user state tree for one-time migration; if the service identity has no home, the shared site-data root still resolves normally.

## Live safe-pause enforcement

The workflow-context Pause and execution-mode values are only early admission hints; they are not treated as authoritative current values. Before a new Codex task starts, the workflow reads the current repository Actions variables after acquiring the Codex concurrency slot. The Python cycle controller reads them again after acquiring the repository execution lock and before it enters durable-state recovery or starts a task, then checks them once more before dispatching the next continuous cycle after an owner merge. The recovery coordinator also reads the live mode and Pause values before classifying a retry and again after its backoff immediately before dispatch.

An absent Pause variable (HTTP 404) means the documented default false; an absent execution-mode variable means continuous. Any other API failure, malformed response, or unsupported value fails closed and prevents a new task/recovery dispatch. When live mode is scheduled, queued push/schedule runs and automatic recovery runs are skipped; explicit manual tasks remain allowed; the Resume endpoint marks its single empty-task dispatch with the explicit resume_once input so an automatic chain cannot masquerade as a one-shot resume. A pause received after a task has already begun does not terminate that task; it prevents the next task from starting after the current safe boundary.

