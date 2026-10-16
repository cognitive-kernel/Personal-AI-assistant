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

- This is a low-level execution primitive only. It is not registered as a task handler and is not exposed through an API yet.
- No arbitrary source code is executed by the application as part of this change.
- There is no network access from the container, no secrets or host directories are mounted, and no persistent output directory is created.
- Docker daemon access is a privileged host capability; deployment must protect the Docker socket and restrict who can configure the executable/image.
- The first version is Python-only. It does not install dependencies, execute shell commands, or attempt to alter the host environment.
- A passing run proves only the result under this exact image, limits, source, and environment.

## Next steps before connecting to learning tasks

1. Verify an official image digest for each supported platform and document the supply-chain check.
2. Add fault-injection tests for timeout, container-client termination, oversized output, and concurrent cleanup.
3. Define an explicit task payload schema and checkpoint/result evidence record before registering a handler.
4. Keep remote execution disabled until provider authorization, quota, checkpoint transfer, and failure recovery contracts are complete.
