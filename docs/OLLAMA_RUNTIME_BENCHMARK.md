# Ollama runtime benchmark

This benchmark measures repeatable local inference timing and hardware snapshots. It does not grade answer quality and does not store prompt or generated text in the report.

## Run on the target host

Run from the repository root with the intended Python environment and the local Ollama service available:

```bash
python scripts/benchmark_ollama_runtime.py --purpose chat --repeats 2 --output /path/outside/repository/ollama-runtime-baseline.json
```

Use `--purpose learning` or `--purpose programming` to measure another profile. Use `--keep-alive 0`, `1m`, `5m`, `10m`, or `30m` to override the profile setting for a comparison; omit it to use the saved policy. Each run executes the same three short English/Persian/code prompts, with 1–10 repetitions per case.

## What is recorded

- Repository commit, Python version, profile, configured model name, Ollama model digest and size when available.
- Context/thread limits, keep-alive duration, wall-clock latency, Ollama model-load/prompt-evaluation/generation durations, token counts and generation tokens per second.
- CPU/RAM snapshots and GPU telemetry where available.

The report deliberately excludes prompt text, generated responses, host name and local paths. The benchmark needs access to the local Ollama service and uses the application's loopback-only request client, resource preflight and GPU-off policy. With GPU disabled, the application's request client sends `num_gpu=0` to Ollama.

## Comparison rules

For a valid before/after comparison, keep the model digest, profile, benchmark version, repeat count, context/thread settings and keep-alive policy equivalent. Save each report outside the repository and compare the summary and per-run timings. Model-load duration can be zero or small on warm runs; use the wall time and generation throughput together. Hardware snapshots are observations around the run, not exact peak utilization.

A successful benchmark is not a quality assessment. Check answer correctness separately with the versioned answer-quality evaluation and human adjudication. Running this tool against a live model is a target-host operation and is intentionally not part of ordinary CI.
