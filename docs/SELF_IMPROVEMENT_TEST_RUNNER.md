# Isolated project test runner image

This image is a trusted administrator-built dependency image for the self-improvement test gate. Candidate project files are mounted read-only at runtime and tests run with networking disabled.

Build only from a verified digest-pinned official Python base. Replace the placeholder with the digest verified from the official Python image manifest:

```sh
docker build \
  --build-arg PYTHON_BASE='python:3.11-slim@sha256:<verified-64-hex-digest>' \
  -f docker/self-improvement-test-runner.Dockerfile \
  -t personal-ai-assistant-test-runner:local .
```

After reviewing the build, get the local immutable image ID:

```sh
docker image inspect --format '{{.Id}}' personal-ai-assistant-test-runner:local
```

Set `SANDBOX_TEST_IMAGE` to that exact `sha256:<64-hex-image-id>` value. The runtime uses `--pull=never`; do not configure a mutable tag. Rebuild and review the image when dependencies need updating.

The image build installs the project's runtime and development dependencies. That build step may require package-index access; test execution itself is offline and uses `--network=none`. Never use a test image from an untrusted source. A passing test run is evidence of behavior against the included test suite, not a security proof.
