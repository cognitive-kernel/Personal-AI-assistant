# Autonomous local Ollama engineering

## Purpose

This workflow replaces the paid OpenAI API dependency with the local Codex CLI connected to Ollama. A Windows self-hosted GitHub Actions runner on the target computer executes the coding session. GitHub-hosted runners perform candidate validation and draft-PR publication.

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

The default agent model is `gpt-oss:20b`. This machine will run inference primarily on CPU, so coding sessions may be slow. Ollama's Codex integration recommends a context window of at least 64k tokens. Actual memory use and throughput must be measured on the target host before treating this as production-ready. Do not silently substitute a smaller model if tool-calling or context requirements are not met.

The autonomous-engineering page lets the operator select an installed Ollama model or enter another model tag. The selected model is stored as the repository Actions variable `AUTONOMOUS_ENGINEERING_MODEL`; the runner verifies that exact model is installed before invoking Codex. If a different model is selected, pull it on the Windows host first (for example, `ollama pull qwen3:14b`). The fallback remains `gpt-oss:20b`.

## One-time Windows setup

1. Install Ollama and verify that its local server is available at `http://127.0.0.1:11434`.
2. Download the selected model:

   ```powershell
   ollama pull gpt-oss:20b
   ```

3. Configure the Codex integration and context settings once:

   ```powershell
   ollama launch codex --config
   ```

   Follow the Ollama setup to select the installed local model and configure a context window of at least 64k. The workflow invokes `codex exec --oss` non-interactively; it does not use an OpenAI API credential.

4. Install Node.js and Codex CLI:

   ```powershell
   npm install -g @openai/codex
   codex --version
   ```

5. Install GitHub CLI and authenticate as the Windows account that runs the local application:

   ```powershell
   gh auth login
   gh auth status
   ```

   The local dashboard uses this existing GitHub CLI authentication only when the user explicitly dispatches or cancels a workflow, or requests recent run status. The app never reads or displays the stored token.

6. In the repository, open **Settings → Actions → Runners → New self-hosted runner**. Select **Windows x64**, follow GitHub's current download/configuration commands, and add the custom label `ollama`. Do not paste the temporary runner registration token into ChatGPT, source files, issue comments, or logs.

7. Configure the runner to run as a Windows service under a dedicated, non-administrator Windows service account. Keep this account separate from the account that runs the local application. The runner account must be able to reach the local Ollama server, but must not be signed in to GitHub CLI and must not contain personal files or unrelated credentials. The local application account retains the GitHub CLI authentication used by the dashboard's explicit dispatch/cancel controls. If Ollama only runs in an interactive tray session, configure Ollama to start persistently under its own secured account/service before relying on unattended runs. Do not run the runner as an administrator unless a separately reviewed requirement proves it necessary.

8. Configure the shared progress directory once from an elevated PowerShell window. Supply the exact Windows service identity used by the Runner and the Windows account that runs the local application:

   ```powershell
   .\scripts\configure_autonomous_engineering_progress.ps1 -RunnerAccount "DOMAIN\runner-svc" -AppAccount "DOMAIN\app-user"
   ```

   The script grants access only to those two accounts, SYSTEM, and local administrators. The directory is `%ProgramData%\PersonalAIAssistant\autonomous-engineering`; both the local app and the runner use this same location. Do not grant access to broad groups such as Everyone or Users.

9. In **Settings → Actions → General → Workflow permissions**, allow GitHub Actions to create pull requests. This is used only by the final GitHub-hosted publication job.

10. From the project root, run the read-only readiness check:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\check_autonomous_agent.ps1
   ```

11. Start the local app and open `http://127.0.0.1:8000/autonomous-engineering`. When all readiness checks pass, use **اجرای یک چرخهٔ مهندسی** or start the workflow from GitHub Actions.

## Workflow behavior

- GitHub checks the schedule hourly at minute 17 UTC. The operator selects 1, 2, 3, 4, 6, 8, 12, or 24 scheduled runs per UTC day in the page; the workflow gates hourly ticks to the selected count. The default is one run per day. Manual dispatch is separate and can add an extra run.
- The selected daily frequency is stored in the repository Actions variable `AUTONOMOUS_ENGINEERING_RUNS_PER_DAY`. The model and frequency are saved from the page using the local GitHub CLI account, which must have permission to manage repository Actions variables.
- The Codex job runs only on a runner carrying `self-hosted`, `Windows`, `X64`, and `ollama` labels and only when the ref is `main`.
- The local job checks that Ollama, the configured model, and Codex CLI are present before starting.
- After Codex proposes changes, the Windows self-hosted runner executes the full default pytest suite and the repository artifact, Markdown-link, import-cycle, retrieval, and claim-to-test quality gates against the modified working tree. The default pytest configuration excludes only the separately marked browser E2E and Docker sandbox integration tests; those remain covered by their dedicated CI workflows.
- A read-only GitHub API snapshot is created before Codex runs. The workflow token is not passed to the Codex process.
- Codex is invoked using `codex exec --oss --sandbox workspace-write`. Its proposed patch is restricted to `app/`, `tests/`, `docs/`, and `pyproject.toml`.
- Candidate code is transferred as an artifact to a separate GitHub-hosted job. That job applies the patch to the exact base SHA and runs the configured Linux test and quality checks without access to the local runner or any API credential.
- A separate GitHub-hosted job with narrow write permissions creates a draft PR only after validation succeeds.
- The agent never writes directly to `main`, merges, deploys, changes secrets/security settings, or modifies real databases.

## Dashboard

The local page at `/autonomous-engineering` reports Ollama reachability, selected-model presence, Codex CLI version, Runner.Listener process state, GitHub CLI authentication, and recent workflow runs. It provides explicit manual dispatch and cancellation controls. It does not install the runner or silently start privileged Windows services.

The page also separates two progress measures:

- **Current cycle progress:** workflow phase progress plus the completed steps in Codex's structured task plan. Codex runs with `--json`; the wrapper records bounded activity events and never publishes raw command output or model reasoning. The plan file is temporary and excluded from the candidate patch.
- **Project progress:** the engineering-completion and overall-acceptance percentages from `docs/ACCEPTANCE_SCORECARD.md`. A dated snapshot is recorded after a worker run so daily, weekly, and custom date-range charts can show verified changes. Historical points are not fabricated or backfilled.

The app polls the lightweight progress endpoint every five seconds and draws the chart with native SVG; no charting dependency or full-page reload is used. Activity details are collapsed by default. Progress state and its bounded event history are stored under `%ProgramData%\PersonalAIAssistant\autonomous-engineering`. The Runner service account and local app account must both have access, configured by the one-time ACL script above. If that directory is not accessible, the page reports that live telemetry is unavailable rather than inventing values.

The AI-learning page and autonomous-engineering page both include the primary navigation menu. The main dashboard also links to the autonomous-engineering page.

## Security and operational limitations

- A self-hosted runner executes repository workflow code on the local host. Use a dedicated, non-administrator service account, separate from the local app user's GitHub CLI account; do not store a personal GitHub CLI login or unrelated credentials in the runner account. Do not register this machine for arbitrary repositories or untrusted PR workflows.
- This workflow intentionally has no `pull_request` or `pull_request_target` trigger. Do not add either trigger to the self-hosted job.
- Keep the runner's working directory and Windows account separate from personal documents, browser profiles, and unrelated credentials where practical.
- Keep the app bound to loopback. The dashboard controls are not authentication; local processes on the computer can still access the local API.
- A persistent runner can wait for jobs around the clock, but actual work occurs in finite workflow runs. The hourly schedule can be delayed, and the computer, network, Ollama server, and runner service must remain available.
- Local CPU inference can be much slower than hosted inference. The target-host performance/soak run, security review, human review, and required repository acceptance gates remain mandatory.
- The UI's readiness indicator is diagnostic only. A green state does not prove model quality, security, or successful workflow execution.

## Troubleshooting

- **Ollama offline:** start Ollama and verify `Invoke-RestMethod http://127.0.0.1:11434/api/tags`.
- **Model missing:** run `ollama pull gpt-oss:20b` (or the configured model).
- **Codex missing:** install with `npm install -g @openai/codex`, then restart the runner service so its PATH is refreshed.
- **Runner missing:** inspect the runner service in Windows Services and confirm the custom `ollama` label.
- **Workflow queued:** confirm the runner is online and has all required labels.
- **Manual dispatch unavailable:** authenticate GitHub CLI for the same Windows account that runs the app and verify repository Actions permissions.
- **Model context or memory errors:** check Ollama's loaded model/context configuration and reduce concurrent workloads; do not claim successful operation until a real run completes.
