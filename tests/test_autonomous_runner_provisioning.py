from pathlib import Path


def test_existing_runner_provisioning_discovers_service_and_preserves_account_membership():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")
    workflow = (root / ".github" / "workflows" / "autonomous-codex-engineering.yml").read_text(encoding="utf-8")

    existing_branch = script.split('if (Test-Path -LiteralPath (Join-Path $RunnerRoot ".runner")) {', 1)[1].split(
        "\n$runnerSid = Get-AutonomousWindowsAccountSid -AccountName $runnerIdentity", 1
    )[0]
    assert "Get-AutonomousRunnerService -RunnerRoot $RunnerRoot" in existing_branch
    assert "$service.StartName" in existing_branch
    assert "Get-LocalGroupMember" in existing_branch
    assert "[Security.Principal.WellKnownSidType]::BuiltinAdministratorsSid" in existing_branch
    assert "Stop-Service" not in existing_branch
    assert "WaitForStatus" not in existing_branch
    assert "Remove-LocalGroupMember" not in existing_branch
    assert "stillAdministrator" not in existing_branch
    assert "Ensure-AutonomousStateOwnerLabel" in existing_branch
    assert "Start-Service" in existing_branch
    assert "$services[0] = Get-CimInstance" not in existing_branch

    assert "IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)" in workflow
    assert "The autonomous Runner must use a non-administrator service account" in workflow
