from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runner_preflight_uses_the_resolved_ollama_executable_consistently():
    workflow = (ROOT / ".github" / "workflows" / "autonomous-codex-engineering.yml").read_text(encoding="utf-8")

    assert "$ollama = Get-Command ollama -ErrorAction Stop" in workflow
    assert "& $ollama.Source --version" in workflow
    assert "$ollamaPath" not in workflow


def test_windows_provisioning_uses_the_shared_identity_module():
    script = (ROOT / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")
    module = (ROOT / "scripts" / "AutonomousWindowsIdentity.psm1").read_text(encoding="utf-8")

    assert 'Import-Module (Join-Path $PSScriptRoot "AutonomousWindowsIdentity.psm1")' in script
    assert "ConvertTo-AutonomousWindowsAccountName" in module
    assert "Get-AutonomousLocalAccountName" in module
    assert "Get-AutonomousWindowsAccountSid" in module
    assert "Get-AutonomousWindowsAccountSid -AccountName $appIdentity" in script
    assert "Get-AutonomousWindowsAccountSid -AccountName $runnerIdentity" in script
    assert 'if ($value.Contains(' in module
    assert 'if ($value.Contains("@"))' not in module


def test_windows_provisioning_does_not_require_a_fixed_users_group_or_admin_acl_grants():
    script = (ROOT / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")
    module = (ROOT / "scripts" / "AutonomousWindowsIdentity.psm1").read_text(encoding="utf-8")

    assert "Add-LocalGroupMember" not in script
    assert "S-1-5-32-545" not in script
    assert "BUILTIN" not in script + module
    assert '"SYSTEM:(OI)(CI)F"' not in script + module
    assert "Set-AutonomousDirectoryAcl -Path $RunnerRoot -RunnerSid $runnerSid" in script
    assert "Set-AutonomousDirectoryAcl -Path $progressPath -RunnerSid $runnerSid -ReadOnlySid $appSid" in script
    assert '$modifySids = @($RunnerSid)' in module
    assert '*$($_):(OI)(CI)M' in module
    assert '*$($ReadOnlySid):(OI)(CI)RX' in module


def test_windows_provisioning_keeps_runner_least_privilege_as_a_role_invariant():
    script = (ROOT / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")

    assert 'Get-LocalGroup -SID "S-1-5-32-544"' in script
    assert "Runner account is a direct member of the local Administrators group" in script
    assert "RunnerAccount and AppAccount must be non-empty Windows account identities." in script
