# Target-host acceptance baseline

This tool creates a redacted report for the exact checkout and configured local SQLite stores. It is a preparation step for target-machine acceptance, not a security certification or a model-quality result.

Run from the repository root in the intended Python environment:

```bash
python scripts/run_target_host_baseline.py
python scripts/run_target_host_baseline.py --run-tests --output /path/outside/repository/target-host-baseline.json
```

On Windows PowerShell, use an absolute output path appropriate to the host, for example:

```powershell
python .\scripts\run_target_host_baseline.py --run-tests --output C:\Temp\target-host-baseline.json
```

## What it checks

- Exact Git commit and whether the working tree is clean.
- Python/platform metadata and installed package versions.
- Read-only health of the configured settings, learning, and cloud-resource SQLite stores: integrity, foreign-key violations, migration history, schema fingerprint, and expected schema version.
- Local-only network configuration: the application bind must be a loopback IP literal, and the effective Ollama endpoint must satisfy the same loopback-only URL policy as the client. The report records only classifications, never the configured host or URL.
- When `--run-tests` is supplied, the full pytest suite's exit code and JUnit counts.

## Safety and interpretation

- Storage diagnostics open existing databases in read-only mode. They do not create databases, migrate schemas, repair data, or include filesystem paths in the report.
- Test execution is opt-in because it can take time. The test suite should still be run only in the intended development/acceptance environment; do not assume that passing CI substitutes for running on the target host.
- Raw test output is intentionally omitted from the JSON report because test output can contain local paths or configuration details.
- Exit code is `0` only when storage is healthy, the configured network boundary is safe and any requested test run passes. A non-loopback or unverified bind, a non-loopback/invalid Ollama URL, missing or outdated databases, or a failing requested test run produces a non-zero result; the script does not attempt automatic repair.
- This does not run a live Ollama factuality evaluation, browser workflows, Docker sandbox validation, crash/load tests, or an independent security review. Those remain separate acceptance gates.


## اندازه‌گیری زندهٔ Ollama (پس از آماده‌شدن میزبان)

پس از نصب وابستگی‌ها و راه‌اندازی پایگاه تنظیمات و Ollama محلی، از ریشهٔ مخزن اجرا کنید:

```powershell
python scripts/benchmark_learning_runtime.py --iterations 3 --timeout 60 --output artifacts/learning-runtime.json
```

گزارش شامل نسخهٔ Python و پلتفرم، تعداد CPU منطقی، RAM در دسترس، محدودیت context/thread، نام و digest مدل، latency میانه و p95، خطاهای هر تکرار و RSS نمونه‌برداری‌شدهٔ پردازه‌های Ollama/model runner است. اسکریپت فقط درخواست loopback محدود اجرا می‌کند و به وب یا API ابری متصل نمی‌شود. اندازه‌گیری RSS نمونه‌برداری‌شده است، نه peak دقیق سیستم‌عامل؛ benchmark کوتاه نیز جایگزین آزمون تولید درس، بار پایدار، یا آزمون نهایی میزبان نیست. فایل گزارش ممکن است مشخصات محیط را داشته باشد و به‌صورت پیش‌فرض نباید commit شود.


## Context برای تولید درس

مقدار پیش‌فرض context برای نصب تازه ۸۱۹۲ است؛ \`runtime_limits\` آن را به ۲۰۴۸ برای RAM کمتر از ۱۲GB و ۴۰۹۶ برای RAM بین ۱۲ و ۲۴GB محدود می‌کند. برای سازگاری با نصب‌های قدیمی که مقدار پیش‌فرض ۲۰۴۸ را در SQLite دارند، مسیر تولید درس در صورت تشخیص RAM کافی context بزرگ‌تری انتخاب می‌کند. این تنظیم صرفاً ظرفیت prompt/خروجی را بهتر می‌کند؛ latency، RSS، پایداری و کیفیت مدل باید روی میزبان واقعی benchmark شوند.
