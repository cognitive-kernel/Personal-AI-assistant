# Isolated execution sandbox — first implementation boundary

## Implemented primitive

`app/sandbox.py` provides a bounded Python execution primitive using an already-installed Docker engine and an already-present, digest-pinned official Python image.

The container is started with:

- no network (`--network=none`);
- no image pull at execution time (`--pull=never`);
- read-only container root and read-only bind mount for the source file;
- dropped Linux capabilities and `no-new-privileges`;
- a non-root numeric user;
- 256 MiB memory, one CPU, 64 PIDs, and a small `/tmp` tmpfs;
- a strict timeout and bounded stdout/stderr per stream;
- a restricted environment for the Docker CLI, with application/Kaggle secrets excluded.

The source file is stored in a temporary directory and removed after the Docker client exits. The result separates `succeeded`, `failed`, `timed_out`, `paused`, `cancelled`, `output_limit_exceeded`, and `unavailable`. A supplied task-control callback is checked while the container runs; pause, cancellation, or loss of control/lease state stops the Docker client and fails closed. Lack of Docker never falls back to running source code directly on the host.

## Image and setup requirements

The image must match the official Python image name and include a SHA-256 digest, for example a locally verified `python:3.12-slim@sha256:<64 lowercase hex characters>`. The exact digest must be selected and verified for the target architecture before use. The sandbox will not download or update the image automatically; administrators must provision it separately.

## Deliberate limitations

- The low-level primitive is wrapped by `app/sandbox_tasks.py` as the allow-listed task type `sandbox.run_python`. The handler is registered only when `SANDBOX_ENABLED=true` (or `1`/`yes`/`on`) and the administrator provides a valid absolute `SANDBOX_DOCKER_EXECUTABLE` and a digest-pinned official `SANDBOX_PYTHON_IMAGE`. Submission is exposed only through the loopback-only, consent-aware `/api/sandbox` routes; the generic `/api/tasks` route rejects this task type.
- Arbitrary source code is never executed during startup or without the configured Docker sandbox, explicit persisted consent, and a user-submitted bounded run request.
- There is no network access from the container, no secrets or host directories are mounted, and no persistent output directory is created.
- Docker daemon access is a privileged host capability; deployment must protect the Docker socket and restrict who can configure the executable/image.
- The first version is Python-only. It does not install dependencies, execute shell commands, or attempt to alter the host environment.
- A passing run proves only the result under this exact image, limits, source, and environment.

## Next steps before connecting to learning tasks

1. Verify and provision an official image digest for each supported platform; the application deliberately does not guess a digest or pull images.
2. Preserve the dedicated loopback-only submission API and keep consent, revocation, bounded-input, and task-control regression tests mandatory before connecting sandbox execution to additional workflows.
3. Keep fault-injection coverage for timeout, container-client termination, oversized output, and concurrent cleanup in CI. The cleanup race is idempotent only when a follow-up Docker listing confirms absence; a still-present container remains an error.
4. Keep remote execution disabled until provider authorization, quota, checkpoint transfer, and failure recovery contracts are complete.


## Allow-listed task adapter

The task payload accepts only `source_code`, `timeout_seconds` (1–120, default 10), and `max_output_bytes` (1–65536, default 32768). It cannot choose the Docker executable or image. The handler records a SHA-256 source digest and execution metadata in task checkpoints/results, not the source itself; the source remains in the task payload because it is the input being tested. Execution remains disabled unless the operator explicitly sets `SANDBOX_ENABLED` and supplies the local Docker path plus a verified image reference.

This adapter does not install dependencies, enable network access, expose an HTTP endpoint, or run arbitrary payloads outside the registered sandbox handler.


## Consent and local API

The loopback-only `/api/sandbox` routes expose consent status/history, explicit consent grant, revocation, and bounded task submission. Consent defaults to off and is stored in the settings database with a policy version and append-only grant/revoke events. The generic `/api/tasks` endpoint rejects `sandbox.run_python` so callers must use the consent-aware route.

The handler checks current consent before execution and periodically while the container runs. Revocation causes the sandbox client to stop at its next control poll; queued tasks also fail closed if consent is absent when they begin. This is an explicit local consent mechanism, not a claim of strong user authentication: the API remains loopback-only and the host account/Docker daemon must be protected.


## Orphan-container cleanup (explicit, conservative)

Each task-backed container receives the application ownership label
`com.cognitivekernel.personal-ai-assistant.sandbox=true` and its durable task ID.
The helper `sandbox.cleanup_orphaned_containers()` is invoked by the task runtime's
serialized maintenance callback only when sandbox execution is explicitly enabled.
Each pass occurs after a worker poll, which runs queue lease reconciliation first.
The callback obtains task IDs in terminal states (`succeeded`, `failed`, or
`cancelled`) from the durable task store and passes them to the cleanup helper. The
helper inspects Docker labels/state/creation time and removes only matching
application containers older than a grace period (60 seconds by default).

Active or unknown task IDs, recent containers, malformed inspect output, invalid
container IDs, and Docker command failures are skipped or reported without deletion.
It uses argument arrays, a restricted Docker CLI environment, bounded command
timeouts, and no shell. If two runtime processes race to remove the same terminal
container, a failed second `docker rm` is treated as an idempotent skip only after a
filtered Docker listing confirms that the container is absent; a still-present
container or failed verification remains an error. The explicit caller must not infer
terminal state from lease expiry alone; first reconcile the durable task state.
Automatic startup integration is deferred until multi-process reconciliation and
recovery ordering are tested.


### Recovery-order guarantees

The runtime calls maintenance only after `TaskWorker.run_once()`; the worker claims
through `claim_next_task()`, which reconciles expired leases before returning. A
task with a valid active lease is absent from the terminal-task query. An expired
single-attempt sandbox task is made terminal before its ID can be considered, and
the terminal-age grace period delays any container removal for another 60 seconds.
Regression tests cover both the active-task and expired-lease paths.


## Real-Docker integration validation

The normal CI suite uses mocked Docker execution for deterministic unit tests. The workflow `.github/workflows/sandbox-integration-ci.yml` now runs the real-Docker integration suite on pull requests and pushes that touch sandbox implementation, integration tests, its own workflow, or project dependencies. It pins the official Python 3.11 slim Linux/amd64 manifest by SHA-256 digest and has read-only repository permissions.

The manual workflow, `.github/workflows/sandbox-integration.yml`, remains available for an operator-selected, official Python image digest. Use **Actions → Sandbox integration → Run workflow** and supply a verified digest-pinned reference. Both workflows check non-root execution, read-only root/mounts, no network, resource limits, timeout behavior, output limits, and absence of host-execution fallback.

The automated job makes real-container behavior a repeatable acceptance gate for relevant code changes; it does not replace a local deployment test. Passing unit tests alone does not prove Docker runtime behavior. The Ollama network-boundary issue #52 remains open pending an independent security review.
