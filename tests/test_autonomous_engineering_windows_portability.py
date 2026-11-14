from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runner_preflight_uses_the_resolved_ollama_executable_consistently():
    workflow = (ROOT / ".github" / "workflows" / "autonomous-codex-engineering.yml").read_text(encoding="utf-8")

    assert "$ollama = Get-Command ollama -ErrorAction Stop" in workflow
    assert "& $ollama.Source --version" in workflow
    assert "$ollamaPath" not in workflow


def test_windows_provisioning_accepts_resolvable_external_account_identities():
    script = (ROOT / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")

    assert "function ConvertTo-WindowsAccountName" in script
    assert '$value.Contains("\\\\") -or $value.Contains("@")' in script
    assert "Get-AccountSidValue $appIdentity" in script
    assert "Get-AccountSidValue $runnerIdentity" in script
    assert "Using externally managed Runner identity" in script
    assert "Get-LocalUser -Name $runnerLocalName" in script


def test_windows_provisioning_does_not_require_a_fixed_users_group_or_grant_admin_acl():
    script = (ROOT / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")

    assert "Add-LocalGroupMember" not in script
    assert "S-1-5-32-545" not in script
    assert "BUILTIN\\\\Administrators" not in script
    assert '"SYSTEM:(OI)(CI)F"' not in script
    assert '$runnerGrant = "*$($runnerSid):(OI)(CI)M"' in script
    assert '$progressGrants += "*$($appSid):(OI)(CI)RX"' in script


def test_windows_provisioning_keeps_runner_least_privilege_as_a_role_invariant():
    script = (ROOT / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")

    assert 'Get-LocalGroup -SID "S-1-5-32-544"' in script
    assert "Runner account is a direct member of the local Administrators group" in script
    assert "RunnerAccount and AppAccount must be non-empty Windows account identities." in script
