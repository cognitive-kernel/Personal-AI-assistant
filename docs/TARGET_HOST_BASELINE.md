# Target-host acceptance baseline

This tool creates a redacted report for the exact checkout and configured local SQLite stores. It is a preparation step for target-machine acceptance, not a security certification or a model-quality result.

Run from the repository root in the intended Python environment:

```bash
python scripts/run_target_host_baseline.py
python scripts/run_target_host_baseline.py --run-tests --run-performance-benchmark --run-model-benchmark --output /path/outside/repository/target-host-baseline.json
```

On Windows PowerShell, use an absolute output path appropriate to the host, for example:

```powershell
python .\scripts\run_target_host_baseline.py --run-tests --run-performance-benchmark --output C:\Temp\target-host-baseline.json
```

## What it checks

- Exact Git commit and whether the working tree is clean.
- Python/platform metadata and installed package versions.
- Read-only health of the configured settings, learning, and cloud-resource SQLite stores: integrity, foreign-key violations, migration history, schema fingerprint, and expected schema version.
- Local-only network configuration: the application bind must be a loopback IP literal, and the effective Ollama endpoint must satisfy the same loopback-only URL policy as the client. The report records only classifications, never the configured host or URL.
- When `--run-tests` is supplied, the full pytest suite's exit code and JUnit counts.
- When `--run-performance-benchmark` is supplied, a disposable SQLite task-queue benchmark records enqueue and claim/complete throughput, database size, and integrity on this exact host. It uses temporary databases and does not benchmark or mutate the configured application stores. Use `--benchmark-tasks` to set the bounded workload and `--min-queue-tps` to set the minimum claim/complete throughput; the default acceptance floor is 10 tasks/second. A benchmark that completes correctly but misses this floor fails the baseline.\n- When `--run-model-benchmark` is supplied, the configured loopback Ollama model performs one warm-up and a bounded number of synthetic Persian generations. The report records the exact model tag/digest, median and maximum request latency, and median generated tokens/second without retaining prompt or output text. Use `--model-benchmark-runs`, `--min-model-tps`, and `--max-model-latency-seconds` to configure its thresholds. Missing model, invalid metrics, timeout, or a missed threshold fails the requested gate.

## Safety and interpretation

- Storage diagnostics open existing databases in read-only mode. They do not create databases, migrate schemas, repair data, or include filesystem paths in the report.
- Test execution is opt-in because it can take time. The test suite should still be run only in the intended development/acceptance environment; do not assume that passing CI substitutes for running on the target host.
- Raw test output is intentionally omitted from the JSON report because test output can contain local paths or configuration details.
- Exit code is `0` only when storage is healthy, the configured network boundary is safe and any requested test run passes. A non-loopback or unverified bind, a non-loopback/invalid Ollama URL, missing or outdated databases, or a failing requested test run produces a non-zero result; the script does not attempt automatic repair.
- The local model benchmark measures inference performance only; it does not assess factual correctness or learning quality. This does not replace the versioned answer-quality benchmark, browser workflows, Docker sandbox validation, crash/load tests, or independent security review. Queue and model benchmarks are bounded workloads and do not establish long-duration stability or production readiness.
