from pathlib import Path


def test_existing_runner_provisioning_enforces_least_privilege_and_restarts_service():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")
    workflow = (root / ".github" / "workflows" / "autonomous-codex-engineering.yml").read_text(encoding="utf-8")

    existing_branch = script.split('if (Test-Path -LiteralPath (Join-Path $RunnerRoot ".runner")) {', 1)[1].split(
        '\nif ((Test-Path -LiteralPath $RunnerRoot)', 1
    )[0]
    assert 'Get-LocalGroup -SID "S-1-5-32-544"' in existing_branch
    assert "Get-LocalGroupMember" in existing_branch
    assert "Stop-Service" in existing_branch
    assert "WaitForStatus" in existing_branch
    assert "Remove-LocalGroupMember" in existing_branch
    assert "stillAdministrator" in existing_branch
    assert "Ensure-AutonomousStateOwnerLabel" in existing_branch
    assert "Start-Service" in existing_branch
    assert "service.StartName" in existing_branch
    assert "$services[0] = Get-CimInstance" not in existing_branch

    assert "IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)" in workflow
    assert "The autonomous Runner must use a non-administrator service account" in workflow
