from scripts import autonomous_engineering_progress as progress


def test_progress_cli_imports_codex_config_when_run_as_a_standalone_script(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "autonomous_engineering_progress.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "codex" in result.stdout


def test_codex_smoke_evidence_reports_each_command_without_logging_raw_commands():
    events = [
        {"type": "item.started", "item": {
            "id": "cmd-1", "type": "command_execution",
            "command": "python -c create probe",
        }},
        {"type": "item.completed", "item": {
            "id": "cmd-1", "type": "command_execution",
            "command": "python -c create probe",
            "status": "completed", "exit_code": 0,
            "aggregated_output": "created",
        }},
        {"type": "item.started", "item": {
            "id": "cmd-2", "type": "command_execution",
            "command": "python -c read probe then unlink",
        }},
        {"type": "item.completed", "item": {
            "id": "cmd-2", "type": "command_execution",
            "command": "python -c read probe then unlink",
            "status": "completed", "exit_code": 0,
            "aggregated_output": "CODEX_WORKSPACE_SANDBOX_OK",
        }},
        {"type": "turn.completed"},
    ]

    evidence = progress.codex_smoke_evidence(
        events,
        "CODEX_WORKSPACE_SANDBOX_OK",
        "OLLAMA_PROVIDER_AND_SANDBOX_OK",
        ["probe", "create", "read", "unlink"],
    )

    assert evidence["command_started_count"] == 2
    assert evidence["command_completed_count"] == 2
    assert evidence["command_summaries"] == [
        {
            "id": "cmd-1",
            "status": "completed",
            "exit_code": 0,
            "start_completed_match": True,
            "required_fragments": {
                "probe": True, "create": True, "read": False, "unlink": False,
            },
            "output_marker_present": False,
            "failure_category": None,
            "failure_field_names": [],
            "failure_signature": None,
        },
        {
            "id": "cmd-2",
            "status": "completed",
            "exit_code": 0,
            "start_completed_match": True,
            "required_fragments": {
                "probe": True, "create": False, "read": True, "unlink": True,
            },
            "output_marker_present": True,
            "failure_category": None,
            "failure_field_names": [],
            "failure_signature": None,
        },
    ]
    assert all("command" not in item for item in evidence["command_summaries"])



def test_codex_model_catalog_registers_the_selected_ollama_context():
    model = "gpt-oss:20b-codex-ctx64000"
    path = progress.write_codex_model_catalog(model, context_window=64000)
    catalog = __import__("json").loads(path.read_text(encoding="utf-8"))
    assert len(catalog["models"]) == 1
    entry = catalog["models"][0]
    assert entry["slug"] == model
    assert entry["context_window"] == 64000
    assert entry["max_context_window"] == 64000
    assert entry["shell_type"] == "default"
    assert entry["input_modalities"] == ["text"]
    assert [level["effort"] for level in entry["supported_reasoning_levels"]] == [
        "low", "medium", "high"
    ]



def test_codex_smoke_evidence_classifies_failures_without_retaining_raw_output():
    events = [
        {"type": "item.started", "item": {
            "id": "failed-command", "type": "command_execution",
            "command": "python -c private-command",
        }},
        {"type": "item.completed", "item": {
            "id": "failed-command", "type": "command_execution",
            "status": "failed", "exit_code": 1,
            "aggregated_output": "Access is denied.",
        }},
        {"type": "item.completed", "item": {
            "type": "error", "error": {
                "code": "exec_command_failed",
                "message": "CreateProcess rejected: blocked by policy",
            },
        }},
        {"type": "turn.failed"},
    ]

    evidence = progress.codex_smoke_evidence(events, "MARKER", "", [])

    assert evidence["command_summaries"][0]["failure_category"] == "permission_denied"
    assert evidence["error_event_count"] == 2
    assert evidence["error_categories"] == ["process_launch_rejected", "no_error_detail"]
    assert "message" in evidence["error_summaries"][0]["field_names"]
    assert "code" in evidence["error_summaries"][0]["field_names"]
    signature = evidence["error_summaries"][0]["signature"]
    assert "CreateProcess rejected: blocked by policy" in signature
    assert "C:\\Users" not in signature
    serialized = __import__("json").dumps(evidence)
    assert "C:\\Users" not in serialized
    assert "python -c secret" not in serialized
    assert "private-command" not in serialized



def test_codex_diagnostic_uses_bounded_reasoning_effort_without_changing_default(monkeypatch):
    import pytest
    from scripts import autonomous_engineering_progress as progress

    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH", "64000")
    monkeypatch.setenv("CODEX_MODEL_REASONING_EFFORT", "low")
    arguments = progress.codex_arguments("gpt-oss:20b-codex-ctx64000", "report.md")
    overrides = [
        arguments[i + 1] for i, value in enumerate(arguments[:-1]) if value == "-c"
    ]
    assert 'model_reasoning_effort="low"' in overrides
    assert "sandbox_workspace_write.exclude_slash_tmp=true" in overrides
    assert any(value.startswith("model_catalog_json=") for value in overrides)
    assert "model_context_window=64000" in overrides
    assert not any(value.startswith("model_providers.ollama.") for value in overrides)

    monkeypatch.delenv("CODEX_MODEL_REASONING_EFFORT")
    default_arguments = progress.codex_arguments("gpt-oss:20b-codex-64k", "report.md")
    default_overrides = [
        default_arguments[i + 1]
        for i, value in enumerate(default_arguments[:-1])
        if value == "-c"
    ]
    assert not any(value.startswith("model_reasoning_effort=") for value in default_overrides)
    assert "sandbox_workspace_write.exclude_slash_tmp=true" in default_overrides
    assert any(value.startswith("model_catalog_json=") for value in default_overrides)
    assert not any(value.startswith("model_providers.ollama.") for value in default_overrides)

    monkeypatch.setenv("CODEX_MODEL_REASONING_EFFORT", "unbounded")
    with pytest.raises(ValueError, match="CODEX_MODEL_REASONING_EFFORT"):
        progress.codex_arguments("gpt-oss:20b-codex-64k", "report.md")


def test_codex_context_is_persisted_and_verified_in_both_windows_workflows():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    context_script = (root / "scripts" / "verify_ollama_context.ps1").read_text(encoding="utf-8")
    diagnostic = (root / ".github" / "workflows" / "codex-windows-sandbox-diagnostic.yml").read_text(encoding="utf-8")
    autonomous = (root / ".github" / "workflows" / "autonomous-codex-engineering.yml").read_text(encoding="utf-8")

    assert 'model = $codexModel' in context_script
    assert 'from = $baseModel' in context_script
    assert 'parameters = @{ num_ctx = $selectedContext }' in context_script
    assert 'PARAMETER\\s+num_ctx' in context_script
    assert 'AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH=$selectedContext' in context_script
    assert context_script.count('if ($VerifyOnly) {') == 2
    assert context_script.count('OLLAMA_RUNTIME_CONTEXT_OK') == 1
    assert 'if ($allocated -ne $selectedContext)' in context_script
    assert 'if ($allocated -lt $selectedContext)' not in context_script
    assert '-lt $minimumContextTokens' in context_script
    assert '64000' not in context_script
    assert 'Stop-LoadedCodexVariants -BaseModel $baseModel' in context_script
    assert 'model_weights_already_loaded = $modelWeightsAlreadyLoaded' in context_script
    assert 'remainingPhysicalMemoryBytes' in context_script
    assert '$remainingPhysicalMemoryBytes -lt [int64]$contextSelection.reserve_bytes' in context_script
    assert 'keep_alive = 0' in context_script
    assert 'OLLAMA_RUNTIME_CONTEXT_RELEASED' in context_script
    inventory_step = diagnostic.split("      - name: Inventory installed Ollama models and capabilities", 1)[1].split(
        "      - name: Verify the selected model's allocated Ollama context", 1
    )[0]
    assert 'verbose = $false' in inventory_step
    assert 'verbose = $true' not in inventory_step
    assert 'AUTONOMOUS_ENGINEERING_MODEL=$codexModel' in context_script
    assert '-VerifyOnly' in diagnostic
    assert '-VerifyOnly' in autonomous
    assert 'group: autonomous-local-engineering-${{ github.repository }}' in diagnostic
    target_host_tests = diagnostic.split(
        "      - name: Run adaptive-context regression suite on the target Windows host", 1
    )[1].split("      - name: Inventory installed Ollama models and capabilities", 1)[0]
    assert "python -m venv" in target_host_tests
    assert "tests/test_ollama_context.py" in target_host_tests
    assert "tests/test_ollama_model_metadata.py" in target_host_tests
    assert "tests/test_codex_smoke_diagnostics.py" in target_host_tests
    assert "tests/test_autonomous_engineering.py" in target_host_tests
    ci_gate = diagnostic.split(
        "      - name: Wait for the complete exact-head GitHub CI matrix", 1
    )[1].split("      - name: Prepare the same service-account runtime", 1)[0]
    assert 'required = @("tests", "browser-e2e", "dependency-audit")' in ci_gate
    assert "EXPECTED_HEAD_SHA" in ci_gate
    assert "latest.conclusion -ne \"success\"" in ci_gate
    assert 'Verify persistent Ollama context after Codex smoke' in diagnostic
    assert 'Verify persistent Ollama context after autonomous Codex task' in autonomous
    assert 'steps.context_preflight.outcome == \'success\'' in diagnostic
    assert 'steps.context_preflight.outcome == \'success\'' in autonomous
    assert "feat/autonomous-engineering-complete-20261112" in diagnostic
    assert "fix/autonomous-task-preservation" not in diagnostic
    assert "fix/automation-codex-context-smoke-20261107" not in diagnostic
    assert 'CODEX_WINDOWS_SANDBOX_BACKEND: mxc' in diagnostic
    assert 'CODEX_MODEL_REASONING_EFFORT: low' in diagnostic
    assert "vars.AUTONOMOUS_ENGINEERING_MODEL" in diagnostic
    assert "inputs.model || 'gpt-oss:20b'" not in diagnostic
    assert 'run-name: "Codex Windows Sandbox Diagnostic #' in diagnostic
    cleanup_job = diagnostic.split("  sandbox-diagnostic:", 1)[0]
    assert '$branch = [uri]::EscapeDataString($env:GITHUB_REF_NAME)' in cleanup_job
    assert '/compare/$($run.head_sha)...$env:GITHUB_SHA' in cleanup_job
    assert '$comparison.status -ne "ahead"' in cleanup_job
    assert '$comparison.behind_by -ne 0' in cleanup_job
    assert '$comparison.ahead_by -le 0' in cleanup_job
    assert 'leaving it untouched' in cleanup_job
    assert all("-MaximumRedirection 0" in line for line in autonomous.splitlines() if "http://127.0.0.1:11434" in line and "Invoke-RestMethod" in line)
    assert all("-MaximumRedirection 0" in line for line in diagnostic.splitlines() if "http://127.0.0.1:11434" in line and "Invoke-RestMethod" in line)
    sandbox_job = diagnostic.split("  sandbox-diagnostic:", 1)[1]
    assert "timeout-minutes: 45" in sandbox_job
    assert "timeout-minutes: 25" in sandbox_job
    assert "Use the exec_command tool exactly once" not in diagnostic
    assert "Use the exec_command tool exactly once" not in autonomous
    assert "single self-contained command" in diagnostic
    assert "login set to false" in diagnostic
    assert "single self-contained command" in autonomous
    assert "--expected-agent-message" not in diagnostic
    assert "CODEX_WORKSPACE_SANDBOX_OK" in diagnostic
    assert "Call exec_command once only" in diagnostic
    assert "do not retry, change the command, or print the success marker" in diagnostic
    assert "echo ok>$probeFileName && type $probeFileName && del $probeFileName" in diagnostic
    assert "--require-command-fragment \"echo\" --require-command-fragment \"type\" --require-command-fragment \"del\"" in diagnostic
    assert "python -c \"from pathlib import Path" not in diagnostic

def test_context_preflight_uses_deterministic_variant_from_selected_base_model():
    from pathlib import Path

    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "verify_ollama_context.ps1"
    ).read_text(encoding="utf-8")

    assert '$codexModel = "$baseModel-codex-ctx$selectedContext"' in script
    assert 'from = $baseModel' in script
    assert '$existingContext.Success' in script
    assert "$internalVariantPattern =" in script
    assert "if ($Model -match $internalVariantPattern)" in script
    assert '$legacyPattern = ' in script
    assert '$isLegacyVariant = [regex]::IsMatch(' in script
    assert "Select the original Ollama model to prevent nested variants" in script
    assert "Start-Process -FilePath $pythonPath -ArgumentList $argumentList" in script
    assert "-RedirectStandardOutput $selectionOutputPath -RedirectStandardError $selectionErrorPath" in script
    assert "& python $helperPath" not in script

    root = Path(__file__).resolve().parents[1]
    provision = (root / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")
    app_check = (root / "scripts" / "check_autonomous_agent.ps1").read_text(encoding="utf-8")
    runner_check = (root / "scripts" / "check_autonomous_runner.ps1").read_text(encoding="utf-8")
    assert "internal Codex context variant" in provision
    assert "Base model selection" in app_check
    assert "Base model selection" in runner_check


def test_codex_model_catalog_rejects_non_positive_context(monkeypatch):
    import pytest
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH", "0")
    with pytest.raises(ValueError, match="positive integer"): 
        progress.write_codex_model_catalog("qwen2.5:7b-codex-ctx511")



def test_codex_model_catalog_accepts_a_single_token_context(monkeypatch):
    import json
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH", "1")
    path = progress.write_codex_model_catalog("tiny:latest-codex-ctx1")
    catalog = json.loads(path.read_text(encoding="utf-8"))
    assert catalog["models"][0]["context_window"] == 1


def test_codex_model_catalog_uses_selected_context_from_environment(monkeypatch):
    import json
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH", "10000")
    path = progress.write_codex_model_catalog("qwen2.5:7b-codex-ctx10000")
    catalog = json.loads(path.read_text(encoding="utf-8"))
    assert catalog["models"][0]["context_window"] == 10000


def test_settings_expose_visible_installed_model_selectors_and_manual_context_inputs():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1] / "app" / "static" / "index.html").read_text(encoding="utf-8")
    for profile in ("chat", "learning", "programming"):
        assert f'id="{profile}-model-installed"' in html
        assert f'id="{profile}-model"' in html
        assert f'id="{profile}-ctx" type="number"' in html
        assert f'id="{profile}-model-installed"' in html
    assert 'const result=await api("/api/models")' in html
    assert 'installedSelect?.addEventListener("change"' in html


def test_codex_uses_unique_custom_ollama_provider_without_redefining_reserved_provider(monkeypatch):
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH", "64000")
    arguments = progress.codex_arguments("gpt-oss:20b-codex-ctx64000", "report.md")
    overrides = [
        arguments[index + 1]
        for index, value in enumerate(arguments[:-1])
        if value == "-c"
    ]
    assert 'model_provider="ollama-local"' in overrides
    assert 'model_providers.ollama-local.base_url="http://127.0.0.1:11434/v1/"' in overrides
    assert 'model_providers.ollama-local.wire_api="responses"' in overrides
    assert "model_providers.ollama-local.requires_openai_auth=false" in overrides
    assert "model_providers.ollama-local.stream_idle_timeout_ms=900000" in overrides
    assert not any(value.startswith("model_providers.ollama.") for value in overrides)
    assert "--oss" not in arguments
    assert "--local-provider" not in arguments

def test_windows_runner_cli_checks_use_cmd_shims_and_discover_active_ollama():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    runner_check = (root / "scripts" / "check_autonomous_runner.ps1").read_text(encoding="utf-8")
    workflow = (root / ".github" / "workflows" / "autonomous-codex-engineering.yml").read_text(encoding="utf-8")

    assert "Get-Command npm.cmd" in runner_check
    assert "$npmCommand.Source --version" in runner_check
    assert "$npmVersionExitCode = $LASTEXITCODE" in runner_check
    assert "$npmPrefixExitCode = $LASTEXITCODE" in runner_check
    assert "$codexExitCode = $LASTEXITCODE" in runner_check
    assert "$ollamaExitCode = $LASTEXITCODE" in runner_check
    assert "| Select-Object -First 1)" not in runner_check
    assert "Get-Command codex.cmd" in runner_check
    assert "$codexCommand.Source --version" in runner_check
    assert "Get-Command ollama.exe" in runner_check
    assert 'Get-CimInstance Win32_Process -Filter "Name=\'ollama.exe\'"' in runner_check
    assert "$ollamaExecutable --version" in runner_check

    assert "Get-Command npm.cmd" in workflow
    assert "& $npm.Source install --global" in workflow
    assert "Get-Command codex.cmd" in workflow
    assert 'Get-CimInstance Win32_Process -Filter "Name=\'ollama.exe\'"' in workflow
    assert "C:\\Users\\Admin\\AppData\\Local\\Programs\\Ollama\\ollama.exe" not in workflow

def test_target_diagnostic_matches_recorded_hardware_and_uses_automatic_context():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    diagnostic = (root / ".github" / "workflows" / "codex-windows-sandbox-diagnostic.yml").read_text(encoding="utf-8")
    autonomous = (root / ".github" / "workflows" / "autonomous-codex-engineering.yml").read_text(encoding="utf-8")
    runner_check = (root / "scripts" / "check_autonomous_runner.ps1").read_text(encoding="utf-8")

    assert "i5-12400" in diagnostic
    assert "physicalCores -ne 6" in diagnostic
    assert "logicalProcessors -ne 12" in diagnostic
    assert "totalRamGiB -lt 30" in diagnostic
    assert "Intel.*UHD.*730" in diagnostic
    assert "wmi_adapter_ram_bytes" in diagnostic
    assert "OLLAMA_RUNTIME_DEVICE_ALLOCATION" in diagnostic
    assert "size_vram" in diagnostic
    assert "check_autonomous_runner.ps1" in diagnostic
    assert "WINDOWS_RUNNER_READINESS_CONTRACT_OK" in diagnostic
    assert "github.ref_name != 'fix/autonomous-engineering-show-all-ollama-models-20261112'" in diagnostic
    assert "github.ref_name == 'fix/autonomous-engineering-show-all-ollama-models-20261112' && '10000'" not in diagnostic
    assert "Get-Command node.exe" in diagnostic
    assert 'Node.js version mismatch: expected at least v26.13.0' in diagnostic
    assert 'npm version mismatch: expected at least 11.20.0' in diagnostic
    assert "Node version: $nodeVersion" in diagnostic
    assert "npm version: $npmVersion" in diagnostic
    assert "expected at least 0.40.16" in diagnostic
    assert "PowerShell version:" in diagnostic
    assert "Get-Command npm.cmd" in diagnostic
    assert "Get-Command codex.cmd" in diagnostic
    assert "Name='ollama.exe'" in diagnostic
    assert "Get-CimInstance Win32_UserProfile" in diagnostic
    assert "AppData\\Local\\Programs\\Ollama\\ollama.exe" in diagnostic
    assert "C:\\Users\\Admin\\AppData\\Local\\Programs\\Ollama\\ollama.exe" not in diagnostic

    assert "Get-Command node.exe" in autonomous
    assert "Get-Command python.exe" in autonomous
    assert '$pythonCommand = Get-Command python.exe -ErrorAction Stop' in autonomous
    assert '$pythonVersionNumber -lt [version]"3.11"' in autonomous
    assert 'C:\\Program Files\\Python311\\python.exe' not in autonomous
    assert "python.exe scripts/autonomous_engineering_progress.py" in autonomous
    assert "python scripts/autonomous_engineering_progress.py" not in autonomous
    assert "Get-Command node -ErrorAction" not in autonomous
    assert "Get-Command npm.cmd" in autonomous
    assert "Get-Command codex.cmd" in autonomous
    assert "Get-CimInstance Win32_UserProfile" in autonomous
    assert "AppData\\Local\\Programs\\Ollama\\ollama.exe" in autonomous
    assert "C:\\Users\\Admin\\AppData\\Local\\Programs\\Ollama\\ollama.exe" not in autonomous
    assert "Get-Command npm.cmd" in runner_check
    assert "Get-Command codex.cmd" in runner_check
    assert "Get-Command ollama.exe" in runner_check
    assert "verbose = $false" in runner_check
    assert "$parsedContext -ge 1" in runner_check
    assert "Get-CimInstance Win32_Service" in runner_check
    assert 'Get-CimInstance Win32_Process -Filter "Name=\'Runner.Listener.exe\'"' in runner_check
    assert "[uint32]$_.ParentProcessId -eq [uint32]$service.ProcessId" in runner_check
    assert "Runner.Listener.exe" in runner_check
    assert '$_.Name -like "actions.runner*"' in runner_check
    assert '$_.State -eq "Running"' in runner_check
    assert '[uint32]$_.ProcessId -gt 0' in runner_check
    assert "Service '$($runnerService.Name)' is running as" in runner_check
    assert 'Could not inspect Windows runner services/processes:' in runner_check
    assert 'Get-Process -Name "Runner.Listener"' not in runner_check

def test_active_codex_documentation_matches_adaptive_context_contract():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    status = (root / "docs" / "PROJECT_STATUS.md").read_text(encoding="utf-8")
    operations = (root / "docs" / "operations" / "CODEX_AUTONOMOUS_ENGINEERING.md").read_text(encoding="utf-8")
    adr = (root / "docs" / "architecture" / "ADR-020-codex-adaptive-context.md").read_text(encoding="utf-8")

    assert "حداقل سقف Context برابر ۶۴٬۰۰۰ توکن" not in readme
    assert "اگر ظرفیت امن به ۶۴٬۰۰۰ توکن نرسد" not in readme
    assert "هیچ حداقل ثابت ۶۴٬۰۰۰ یا ۵۱۲ توکنی وجود ندارد" in readme
    assert "PR [#702](" in status
    assert "با قرارداد ثابت ۶۴k در main ادغام شد" in status
    assert "PR #722 پیشنهاد حذف الزام ثابت ۶۴k را می‌دهد" in status
    assert "حداقل Codex یک توکن مثبت" in status
    assert "There is no Codex-specific 64,000-token minimum" in operations
    assert "docs.ollama.com/integrations/codex" in operations
    assert "at least 64k tokens" in operations
    assert "docs.ollama.com/integrations/codex" in adr
    assert "below the upstream-documented recommendation" in adr
    assert "docs.ollama.com/integrations/codex" in readme
    assert "آزمون واقعی فراخوانی ابزار Codex" in readme
    assert "There is no fixed minimum context window" in adr
    assert '"max_context_window": context_window' in (root / "scripts" / "autonomous_engineering_codex_config.py").read_text(encoding="utf-8")
    assert "model_context_window={context_window}" in (root / "scripts" / "autonomous_engineering_codex_config.py").read_text(encoding="utf-8")
    assert "inherited Codex configuration from raising" in adr
    assert "inherited Codex config cannot increase it" in operations
    assert "floor((2^63 - 1) / 9)" in adr
    assert "automatic compaction" in adr
    assert "currently 1,048,576 tokens" not in adr
    assert "تنظیمات قدیمی Codex نتوانند Context بزرگ‌تری تحمیل کنند" in readme

def test_windows_provisioning_and_app_readiness_use_consistent_cli_resolution():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    provision = (root / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")
    app_check = (root / "scripts" / "check_autonomous_agent.ps1").read_text(encoding="utf-8")

    assert "Get-Command ollama.exe" in provision
    assert "Get-CimInstance Win32_Process -Filter \"Name='ollama.exe'\"" in provision
    assert "Get-CimInstance Win32_UserProfile" in provision
    assert "AppData\\Local\\Programs\\Ollama\\ollama.exe" in provision
    assert "Get-Command node.exe" in provision
    assert "Get-Command npm.cmd" in provision
    assert "Get-Command git.exe" in provision
    assert "Get-Command python.exe" in provision
    assert "Get-Command gh.exe" in provision
    assert "Node.js 26.13.0 or newer is required" in provision
    assert "npm 11.20.0 or newer is required" in provision
    assert "Ollama 0.40.16 or newer is required" in provision
    assert "Get-Command ollama -ErrorAction" not in provision
    assert "Get-Command npm -ErrorAction" not in provision

    assert "Get-Command gh.exe" in app_check
    assert "Get-CimInstance Win32_Process -Filter" in app_check
    assert "Get-Process -Name \"Runner.Listener\"" not in app_check


def test_windows_storage_workflow_exercises_adaptive_context_bridge_in_powershell_51():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    bridge = (root / "tests" / "test_ollama_context_bridge.ps1").read_text(encoding="utf-8")

    assert "Verify the adaptive context bridge in Windows PowerShell 5.1" in workflow
    assert "shell: powershell" in workflow
    assert "test_ollama_context_bridge.ps1" in workflow
    assert "Start-Process -FilePath $pythonPath -ArgumentList $argumentList" in bridge
    assert "-RedirectStandardOutput $outputPath -RedirectStandardError $errorPath" in bridge
    assert "selected_context_tokens" in bridge
    assert "requested_context_tokens = [long]1000" in bridge
    assert "model_weights_already_loaded" in bridge
    assert "Available RAM cannot hold the model weights" in bridge
    assert "64000" not in bridge


def test_codex_config_pins_sub_64k_context_to_the_verified_ollama_allocation(monkeypatch):
    import json
    from pathlib import Path

    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH", "1000")
    arguments = progress.codex_arguments("qwen2.5:7b-codex-ctx1000", "report.md")
    overrides = [
        arguments[index + 1]
        for index, value in enumerate(arguments[:-1])
        if value == "-c"
    ]

    assert "model_context_window=1000" in overrides
    catalog_override = next(value for value in overrides if value.startswith("model_catalog_json="))
    catalog_path = json.loads(catalog_override.split("=", 1)[1])
    entry = json.loads(Path(catalog_path).read_text(encoding="utf-8"))["models"][0]
    assert entry["context_window"] == 1000
    assert entry["max_context_window"] == 1000
