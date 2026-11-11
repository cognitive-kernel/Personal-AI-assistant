# Autonomous local Ollama engineering

## Purpose

This workflow replaces the paid OpenAI API dependency with the local Codex CLI connected to Ollama. A Windows self-hosted GitHub Actions runner on the target computer executes one bounded coding task at a time. Each task gets its own non-draft pull request. GitHub-hosted tests, browser E2E, and dependency-audit workflows validate that exact commit. After exact head/base verification and successful checks, the PR remains open for owner review. The controller never merges PRs automatically. After the owner manually merges a validated PR, the controller verifies the exact merge commit and all post-merge checks before clearing the durable task and dispatching the next run from refreshed `main` in continuous mode.

The worker is event-driven and periodic. It is not a model process generating code continuously for 24 hours.

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

There is no default or fallback model. The operator must explicitly select an installed model in the dashboard and save it before the autonomous workflow can run. The selected model must advertise the Ollama `tools` capability and expose an architecture-specific `context_length` of at least 64,000 tokens in `/api/show`; otherwise readiness and saving the selection fail closed. The workflow also refuses to start Codex if the safe hardware capacity is below 64,000 tokens. This machine runs inference primarily on CPU, so coding sessions may be slow. The 64k minimum follows Ollama's current Codex integration guidance; it is not silently lowered to fit an undersized model or host.

The autonomous-engineering page discovers the complete installed-model list from the local Ollama `/api/tags` endpoint; it does not inject a fake default into that list. The selected model is stored as the repository Actions variable `AUTONOMOUS_ENGINEERING_MODEL`. Saving is allowed only when that exact model is installed, its `/api/show` metadata exposes a valid architecture-specific context limit, and its `capabilities` include `tools`. The runner repeats these checks before creating a Codex model variant and rejects models whose architecture-specific maximum is below 64,000 tokens. If another model is selected, pull it on the Windows host first (for example, `ollama pull qwen3:14b`). If no model has been explicitly saved, readiness remains blocked and the workflow refuses to start; no model is selected or substituted automatically. The page also accepts an optional direct engineering task (maximum 2,000 characters) and forwards it as a `workflow_dispatch` input. For a non-empty manual dispatch targeting `main`, a dedicated Windows job checks out trusted `main` without persisting Git credentials and writes the task to `%ProgramData%\PersonalAIAssistant\autonomous-engineering\pending-manual-task.txt` before the schedule gate runs. The job rejects a different already-pending task or an open controller-authored PR; the task remains durable through pause, skipped scheduling, and recovery, and is cleared only after the controller confirms the PR was merged. Codex receives it later as untrusted task data; it cannot override the governing engineering/security prompt. The task text is part of GitHub's workflow-dispatch event metadata, so never include passwords, API keys, tokens, private user data, or other secrets.

## One-time Windows setup

1. Install Ollama for Windows and keep its server available at `http://127.0.0.1:11434`. If it normally runs only in an interactive tray session, configure a persistent service/startup method before relying on unattended runs.
2. Install the model you intend to use before selecting it in the dashboard:

   ```powershell
   ollama pull <selected-model>
   ```

   The workflow reads the selected model's architecture-specific maximum context, installed weight size, KV-cache dimensions, and currently available physical RAM. `app/ollama_context.py` chooses the exact `min(requested context, model maximum, safe hardware capacity)` value and requires the result to be at least 64,000 tokens for Codex; a model maximum or safe hardware ceiling below that threshold stops the workflow. It reserves 4 GiB for Windows/other processes, and budgets at least 0.5 GiB or 10% of model weights for overhead while estimating an FP16 KV cache. A missing/invalid metadata field, missing RAM telemetry, or insufficient headroom stops the workflow; there are no 512/2048-token fallback guesses. The selected value is persisted in `AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH`. The workflow creates a dedicated local model variant named `<selected-model>-codex-ctx<selected-context>` through Ollama `/api/create` with persistent `num_ctx`, verifies the saved `PARAMETER num_ctx` through `/api/show`, loads it without a request-level `options.num_ctx` override, and verifies the actual allocation through `/api/ps`. Codex receives this exact variant and the same context value in its model catalog. The original selected model is not modified, no Ollama server restart or administrator credential is required, and the workflow repeats the runtime allocation check after Codex execution. The variant shares the base model's underlying weights; it is a separate model configuration, not a second model download. The RAM estimate is a preflight estimate, while successful load plus `/api/ps` and the real tool-call smoke test are the runtime acceptance evidence. See the [official context-length guide](https://docs.ollama.com/context-length), [Modelfile reference](https://docs.ollama.com/modelfile), and [API reference](https://github.com/ollama/ollama/blob/main/docs/api.md). A larger context increases memory use; measure stability on the target host.
   
   The model can be pulled under the application/administrator account; the Runner service account only needs access to the local Ollama HTTP endpoint.
3. Install Node.js LTS system-wide so the executable is available to the Windows Runner service.
4. Install GitHub CLI and authorize it once with the **same GitHub account already signed in to Chrome**. Use `gh auth login --web`; GitHub opens its browser authorization flow and the existing Chrome session can be used. This does not create another GitHub account or require a paid service. The authorization is stored locally for the application account and is reused by the dashboard; do not authenticate GitHub CLI as the Runner service account:

   ```powershell
   gh auth login --web
   gh auth status
   ```

5. From an elevated PowerShell window in the repository checkout, provision the Runner and shared progress ACLs in one operation:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\\scripts\\provision_autonomous_runner.ps1 `
     -Repository "cognitive-kernel/Personal-AI-assistant" `
     -RunnerAccount "runner-svc" `
     -AppAccount "app-user"
   ```

   Replace the two local account names with the existing Windows account used by the app and the desired dedicated Runner account. When `-Model` is omitted, the script reads the saved `AUTONOMOUS_ENGINEERING_MODEL` repository variable and verifies that exact model's installation, architecture-specific context metadata, and `tools` capability. The script creates the Runner account as a standard user, obtains a short-lived registration token through the authenticated GitHub CLI, downloads the official latest Windows x64 Runner, verifies GitHub’s published SHA-256 digest, configures the Runner service under the dedicated account, and applies least-privilege ACLs to the Runner and `%ProgramData%\\PersonalAIAssistant\\autonomous-engineering` directories. The official Runner configuration tool prompts for the service-account password; the password is not passed as a process argument. The registration token is not written to a file or printed.

   The script requires an elevated shell and repository **Administration: write** permission. It refuses to overwrite an existing configured Runner or a non-empty unconfigured Runner directory. If a Runner is already configured, it verifies the service identity and starts it without re-registering.

6. In **Settings → Actions → General → Workflow permissions**, allow GitHub Actions to create and approve pull requests if the repository requires it. The autonomous job requests only `contents: write`, `pull-requests: write`, and `actions: write` for the trusted controller. That controller calls GitHub's merge API only after local tests, all three independent exact-SHA workflows, and unchanged head/base checks succeed. Do not disable branch protection or grant a bypass; if repository rules require a human approval that prevents the configured token from merging, the controller must fail closed and report that policy blocker.
7. From the application account and the application checkout, run the app-side readiness check:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\\scripts\\check_autonomous_agent.ps1
   ```

   This checks the local Ollama endpoint/model, application-account GitHub CLI authentication, Runner.Listener visibility, and read access to shared progress storage. Pass the exact saved model via `-SelectedModel <saved-model>`. It intentionally does not require Codex CLI to be installed in the application account.
8. From the Runner service account, run the Runner-side check from its checked-out workspace:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\\scripts\\check_autonomous_runner.ps1
   ```

   This checks that the Runner account is non-administrator, plus Codex CLI, Ollama CLI/server/model, Runner.Listener, and write access to shared progress storage. Pass the exact saved model via `-SelectedModel <saved-model>`. It does not require or use GitHub CLI authentication. The workflow repeats the critical Runner checks before every Codex session.
9. Start the local app and open `http://127.0.0.1:8000/autonomous-engineering`. When application-side readiness passes, use **شروع صف پیوسته** or start the workflow from GitHub Actions. The workflow performs its own Runner-side checks and fails closed if the Runner account is not configured correctly.

## Workflow behavior

- GitHub checks the schedule hourly at minute 17 UTC. Scheduled mode accepts exactly 1, 2, 4, 6, 12, or 24 hours between runs. Continuous mode is the default: a main-branch update or hourly heartbeat starts work automatically when no other run is active; after each owner-verified merge and successful post-merge CI, the controller dispatches the next bounded run immediately. Manual task persistence runs before the schedule gate, including when Pause or active-run suppression prevents execution. The workflow has no workflow-level concurrency group that could replace an unpersisted dispatch; the Codex job is serialized separately, while the active-run guard and repository execution lock prevent overlapping work. Duplicate scheduled/manual/recovery triggers are suppressed by checking active workflow runs. It is not limited by a daily run count.
- The selected interval is stored in `AUTONOMOUS_ENGINEERING_INTERVAL_HOURS` and accepts exactly 1, 2, 4, 6, 12, or 24 hours. The model and interval are saved using the existing GitHub CLI authorization on the Windows host.
- The execution mode is stored in `AUTONOMOUS_ENGINEERING_MODE` (`scheduled` or `continuous`). Scheduled mode checks the hourly cron against the configured interval. Continuous mode starts on a main-branch push or the hourly heartbeat. Each run performs one bounded task, runs the full Windows test suite and repository quality gates, creates a PR, dispatches `tests.yml`, `browser-e2e.yml`, and `dependency-audit.yml` against the exact candidate SHA, verifies the unchanged PR head and `main` base, and leaves the PR open for owner review. After a manual merge, the next run verifies the merge commit and dispatches the three required workflows against that exact post-merge SHA before clearing the durable task and continuing. The first session may consume the submitted manual task. An open autonomous PR blocks duplicate task generation.
- Each workflow run is bounded by GitHub's six-hour job limit and publishes at most one PR. The overall queue is not limited to eight tasks or one six-hour run: continuous mode dispatches a new run after each verified merge. A failed or unverified task is never merged or counted as accepted. The queue stops when paused, empty, blocked, or when verification cannot be repaired. No deployment is performed.
- The Codex job runs only on a runner carrying `self-hosted`, `Windows`, `X64`, and `ollama` labels and only when the ref is `main`.
- The local job checks that Ollama, the configured model, and Codex CLI are present before starting.
- After each Codex task, the Windows self-hosted runner executes the full default pytest suite and the repository artifact, Markdown-link, import-cycle, retrieval, and claim-to-test quality gates against that task's working tree. Before owner review, the three GitHub-hosted workflows independently test the exact PR head, including the browser E2E suite and dependency audit.
- A read-only GitHub API snapshot is created before Codex runs. The workflow token is not passed to the Codex process.
- Codex is invoked using `codex exec --oss --local-provider ollama --sandbox workspace-write`; Ollama is selected explicitly for the Runner account. Its proposed patch is restricted to `app/`, `tests/`, and `docs/` (including `scripts/`).
- The task controller creates one isolated PR per verified task. It explicitly dispatches the repository's Linux test, browser E2E, and dependency-audit workflows on the PR branch and verifies each completed run's branch and head SHA before merge.
- The workflow token is removed from the Codex and test subprocess environments. A trusted publisher subprocess receives it only after local verification, pushes the task branch, creates/reuses a non-draft PR, dispatches exact-commit checks, revalidates the PR head/base, and leaves the PR open. It never calls the PR merge API; after an owner merge, the controller runs post-merge checks on the exact merge SHA.
- The agent never pushes directly to `main` or deploys. The trusted controller may merge only after all local and independent GitHub checks pass and the PR head/base are unchanged. It cannot change secrets/security settings or modify real databases.

## Dashboard

The local page at `/autonomous-engineering` reports Ollama reachability, selected-model presence, the Codex CLI visible to the application account (informational only), Runner.Listener process state, application-account GitHub CLI authentication, shared progress-storage access, and recent workflow runs. The workflow separately verifies Codex CLI and write access under the actual Runner service account before every coding session. It provides explicit manual dispatch and cancellation controls. It does not install the runner or silently start privileged Windows services.

During each Codex session, a detached Windows checkpoint watcher atomically saves the allowlisted worktree diff and agent plan to `%ProgramData%\\PersonalAIAssistant\\autonomous-engineering` every 20 seconds. Before the next checkout cleanup, the workflow stops the previous watcher and captures a final snapshot. A retry restores the checkpoint only when its immutable base SHA still matches `main`; otherwise it preserves the checkpoint and fails closed instead of applying it to a different code base. Checkpoint files stay outside the Git repository and never contain workflow credentials.

The separate `autonomous-codex-recovery.yml` workflow retries failed or timed-out continuous cycles from the last accepted `main` checkpoint. A submitted manual task is stored in the Runner's durable `%ProgramData%\PersonalAIAssistant\autonomous-engineering\pending-manual-task.txt` state before schedule gating, so pause, duplicate-trigger suppression, and recovery do not silently discard it. The controller clears that file only after it confirms the corresponding PR merge. Failed or timed-out cycles retry indefinitely with exponential backoff capped at 4 minutes; there is no automatic retry-count pause. The owner can always use the explicit durable Pause control; Resume clears the failure counter. A retry does not count a failed task as complete.

The page provides durable pause/resume controls. The continuous queue starts automatically after a main-branch update or on the next hourly heartbeat, so no manual first dispatch is required. Pause sets `AUTONOMOUS_ENGINEERING_PAUSED=true`; the current task reaches its safe boundary, and no new task starts. Resume clears the flag and dispatches a new run from the latest persisted project state. Emergency cancel remains separate and terminates the active run.

The page also separates two progress measures:

- **Agent plan progress:** the percentage is the completed/total count of Codex's structured plan items (equal-weight task count, not estimated effort). The current workflow phase and active step are displayed separately; the plan can reach 100% before tests, independent validation, owner review, manual merge, and post-merge checks finish. Codex runs with `--json`; the wrapper records bounded activity events and never publishes raw command output or model reasoning. The plan file is temporary and excluded from the candidate patch.
- **Project progress:** the engineering-completion and overall-acceptance percentages from `docs/ACCEPTANCE_SCORECARD.md`. The workflow snapshots the trusted `main`-branch scorecard before Codex starts. A dated chart point is recorded only when a later run observes changed accepted percentages on `main`; unmerged candidate changes are never recorded, and repeated runs with unchanged scores do not create artificial chart points. This keeps daily, weekly, and custom date-range charts limited to verified progress changes. Historical points are not fabricated or backfilled.

The app polls the lightweight progress endpoint every five seconds and draws the chart with native SVG; no charting dependency or full-page reload is used. Activity details are collapsed by default. Progress state and its bounded event history are stored under `%ProgramData%\PersonalAIAssistant\autonomous-engineering`. The Runner service account and local app account must both have access, configured by the one-time ACL script above. If that directory is not accessible, the page reports that live telemetry is unavailable rather than inventing values.

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
- **Runner missing:** inspect the runner service in Windows Services and confirm the custom `ollama` label.
- **Workflow queued:** confirm the runner is online and has all required labels.
- **Manual dispatch unavailable:** authenticate GitHub CLI for the same Windows account that runs the app and verify repository Actions permissions.
- **Model context or memory errors:** check Ollama's loaded model/context configuration and reduce concurrent workloads; do not claim successful operation until a real run completes.


## Automatic Codex CLI provisioning

The autonomous workflow installs `@openai/codex` automatically on its first run under the dedicated Runner service account. Node.js LTS and npm must be installed system-wide and available in the Runner service PATH. The workflow uses that account's npm global prefix and does not require an OpenAI login or API key because it explicitly selects the local Ollama provider. No GitHub CLI credentials are stored in the Runner account.

This automatic provisioning section supersedes the earlier step 5 instructions to sign in as the Runner account and install Codex CLI manually.
