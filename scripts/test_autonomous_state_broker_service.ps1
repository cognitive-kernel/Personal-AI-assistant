$ErrorActionPreference = "Stop"

$serviceName = "PersonalAIAssistantStateBroker"
$serviceSource = Join-Path $env:RUNNER_TEMP "AutonomousStateBrokerService.exe"
if (-not (Test-Path -LiteralPath $serviceSource -PathType Leaf)) {
    throw "The compiled Windows state broker executable is missing."
}

$serviceRoot = Join-Path $env:ProgramData "PersonalAIAssistant\StateBroker-CI"
$serviceExe = Join-Path $serviceRoot "PersonalAIAssistant.StateBroker.exe"
$stateRoot = Join-Path $env:ProgramData ("PersonalAIAssistant\Broker-CI-" + [guid]::NewGuid().ToString("N"))
$oldMachineStatePath = [Environment]::GetEnvironmentVariable("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "Machine")
$oldDiagnosticsSetting = [Environment]::GetEnvironmentVariable("AUTONOMOUS_STATE_BROKER_DIAGNOSTICS", "Machine")
$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$brokerSid = "S-1-5-19"
$serviceCreated = $false

try {
    $existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    if ($existing) {
        if ($existing.Status -eq "Running") { Stop-Service -Name $serviceName -Force -ErrorAction Stop }
        sc.exe delete $serviceName | Out-Null
        Start-Sleep -Seconds 2
        if (Get-Service -Name $serviceName -ErrorAction SilentlyContinue) {
            throw "A previous state broker service instance could not be removed."
        }
    }

    New-Item -ItemType Directory -Path $serviceRoot, $stateRoot, (Join-Path $stateRoot "run-diagnostics") -Force | Out-Null
    Copy-Item -LiteralPath $serviceSource -Destination $serviceExe -Force
    & icacls.exe $serviceRoot /grant:r "*$($brokerSid):(OI)(CI)RX" /T /C /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not grant LocalService read/execute access to the test service binary." }
    [Environment]::SetEnvironmentVariable("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", $stateRoot, "Machine")
    [Environment]::SetEnvironmentVariable("AUTONOMOUS_STATE_BROKER_DIAGNOSTICS", "1", "Machine")

    $current = @{
        schema_version = 1
        status = "running"
        phase_label = "CI live broker pipe test"
        activity = "Verifying the real Windows named-pipe client"
        percent = 42
        updated_at = "2026-11-17T00:00:00Z"
    }
    $baseline = @{
        date = "2026-11-17"
        engineering_percent = 42
        overall_percent = 31
        sha = "0123456789012345678901234567890123456789"
    }
    [System.IO.File]::WriteAllText(
        (Join-Path $stateRoot "current.json"),
        (ConvertTo-Json -InputObject $current -Compress),
        [System.Text.UTF8Encoding]::new($false)
    )
    [System.IO.File]::WriteAllText(
        (Join-Path $stateRoot "scorecard-baseline.json"),
        (ConvertTo-Json -InputObject $baseline -Compress),
        [System.Text.UTF8Encoding]::new($false)
    )
    [System.IO.File]::WriteAllText(
        (Join-Path $stateRoot "history.jsonl"),
        '{"type":"project_snapshot","date":"2026-11-17","engineering_percent":42,"overall_percent":31,"recorded_at":"2026-11-17T00:00:00Z"}' + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false)
    )
    [System.IO.File]::WriteAllText(
        (Join-Path $stateRoot "events.jsonl"),
        '{"type":"progress_event","updated_at":"2026-11-17T00:00:00Z","phase_label":"CI","activity":"Pipe test","percent":42,"status":"running"}' + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false)
    )
    $diagnostics = @{
        schema_version = 1
        run_id = 12345
        updated_at = "2026-11-17T00:00:00Z"
        diagnostics = @(@{
            run_attempt = "1"
            task = 1
            attempt = 1
            stage = "ci"
            category = "integration"
            summary = "Live broker integration test"
            detail = "The Windows service returned diagnostics through its named pipe."
            recorded_at = "2026-11-17T00:00:00Z"
        })
    }
    [System.IO.File]::WriteAllText(
        (Join-Path (Join-Path $stateRoot "run-diagnostics") "12345.json"),
        (ConvertTo-Json -InputObject $diagnostics -Depth 10 -Compress),
        [System.Text.UTF8Encoding]::new($false)
    )

    Import-Module (Join-Path $PSScriptRoot "AutonomousWindowsIdentity.psm1") -Force
    Set-AutonomousDirectoryTreeAcl -Path $stateRoot -ModifySids @($currentSid, $brokerSid) -OwnerSid $brokerSid -InstallerSid $currentSid

    New-Service -Name $serviceName -BinaryPathName ('"' + $serviceExe + '"') -DisplayName "Personal AI Assistant State Broker CI" -StartupType Manual | Out-Null
    $serviceCreated = $true
    $sc = Get-Command sc.exe -ErrorAction Stop
    $serviceConfigOutput = & $sc.Source config $serviceName 'obj=' 'NT AUTHORITY\LocalService' 'start=' 'demand' 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Could not configure the test broker to run as LocalService: $($serviceConfigOutput -join ' ')"
    }
    Start-Service -Name $serviceName -ErrorAction Stop
    $service = Get-Service -Name $serviceName -ErrorAction Stop
    $service.WaitForStatus([System.ServiceProcess.ServiceControllerStatus]::Running, [TimeSpan]::FromSeconds(20))

    Import-Module (Join-Path $PSScriptRoot "AutonomousStateBroker.psm1") -Force
    try {
        $health = Invoke-AutonomousStateBroker -Method GET -Path "/v1/health" -TimeoutMilliseconds 5000
    } catch {
        $diagnosticPath = Join-Path $stateRoot "service-errors.log"
        $diagnosticText = if (Test-Path -LiteralPath $diagnosticPath) {
            Get-Content -LiteralPath $diagnosticPath -Raw -ErrorAction SilentlyContinue
        } else {
            "No worker diagnostic was recorded."
        }
        throw "PowerShell could not read live broker health: $($_.Exception.Message) Worker diagnostic: $diagnosticText"
    }
    if ($health.ready -ne $true) { throw "PowerShell could not read live broker health." }

    $probe = @'
from app import autonomous_engineering_state_broker as broker
from app.autonomous_engineering_state_broker import StateBrokerConflict

health = broker.request("GET", "/v1/health")
assert health.get("ready") is True, "Python named-pipe health response was not ready."
snapshot = broker.progress()
assert snapshot["storage_configured"] is True
assert snapshot["current"]["phase_label"] == "CI live broker pipe test"
assert len(snapshot["history"]) == 1
assert len(snapshot["events"]) == 1
pending = broker.pending_work()
assert pending["available"] is True and pending["blocked"] is False
diagnostics = broker.diagnostics(12345)
assert diagnostics and str(diagnostics["run_id"]) == "12345"
'@
    $probe | & python -
    if ($LASTEXITCODE -ne 0) { throw "The Python named-pipe client failed its live Windows service integration test." }

    # Verify the same brokered snapshot from a distinct, non-administrator logon.
    $secondaryName = "BrokerCI" + [guid]::NewGuid().ToString("N").Substring(0, 8)
    $secondaryPassword = "Aa9!" + [guid]::NewGuid().ToString("N") + "Zz7!"
    $secondarySecurePassword = ConvertTo-SecureString $secondaryPassword -AsPlainText -Force
    $secondaryCreated = $false
    try {
        New-LocalUser -Name $secondaryName -Password $secondarySecurePassword -PasswordNeverExpires -AccountNeverExpires | Out-Null
        $secondaryCreated = $true
        $secondaryCredential = [System.Management.Automation.PSCredential]::new(
            "$env:COMPUTERNAME\$secondaryName",
            $secondarySecurePassword
        )
        & (Join-Path $PSScriptRoot "test_autonomous_state_broker.ps1") -SecondaryCredential $secondaryCredential -PythonPath (Get-Command python.exe -ErrorAction Stop).Source
    } finally {
        if ($secondaryCreated) {
            $localSecondary = Get-LocalUser -Name $secondaryName -ErrorAction SilentlyContinue
            if ($localSecondary) {
                Remove-LocalUser -InputObject $localSecondary -ErrorAction SilentlyContinue
            }
        }
    }

    # Publish a test checkpoint and verify that the protected Windows broker
    # exposes only its validated summary and refuses progress cleanup.
    $checkpointRunId = "12345"
    $checkpointTask = 1
    $checkpointStem = "worktree-checkpoint-$checkpointRunId-$checkpointTask"
    $checkpointPatch = Join-Path $stateRoot ($checkpointStem + ".patch")
    $checkpointMetadataPath = Join-Path $stateRoot ($checkpointStem + ".json")
    $checkpointIndexPath = Join-Path $stateRoot "latest-worktree-checkpoint.json"
    $privatePatchBytes = [System.Text.Encoding]::UTF8.GetBytes("private checkpoint payload; never return this from the broker")
    [System.IO.File]::WriteAllBytes($checkpointPatch, $privatePatchBytes)
    $checkpointPatchLength = (Get-Item -LiteralPath $checkpointPatch).Length
    $checkpointBaseSha = "c" * 40
    $checkpointUpdatedAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $checkpointMetadata = [ordered]@{
        schema_version = 4
        run_id = $checkpointRunId
        task = $checkpointTask
        base_sha = $checkpointBaseSha
        updated_at = $checkpointUpdatedAt
        patch_bytes = $checkpointPatchLength
        watcher_pid = $null
        watcher_started_at = $null
        watcher_ready = $false
    }
    $checkpointIndex = [ordered]@{
        schema_version = 1
        active = $true
        run_id = $checkpointRunId
        task = $checkpointTask
        base_sha = $checkpointBaseSha
        updated_at = $checkpointUpdatedAt
        patch_bytes = $checkpointPatchLength
        plan_available = $false
    }
    [System.IO.File]::WriteAllText(
        $checkpointMetadataPath,
        (ConvertTo-Json -InputObject $checkpointMetadata -Depth 5 -Compress),
        [System.Text.UTF8Encoding]::new($false)
    )
    [System.IO.File]::WriteAllText(
        $checkpointIndexPath,
        (ConvertTo-Json -InputObject $checkpointIndex -Depth 5 -Compress),
        [System.Text.UTF8Encoding]::new($false)
    )
    $checkpointProbe = @'
from app import autonomous_engineering_state_broker as broker
from app.autonomous_engineering_state_broker import StateBrokerConflict

pending = broker.pending_work()
assert pending["available"] is True and pending["blocked"] is True
checkpoint = pending["checkpoint"]
assert checkpoint["available"] is True and checkpoint["verified"] is True
assert checkpoint["run_id"] == "12345" and checkpoint["task"] == 1
assert checkpoint["patch_bytes"] > 0
assert "private checkpoint payload" not in repr(pending)
assert "ProgramData" not in repr(pending)
try:
    broker.cleanup()
except StateBrokerConflict:
    pass
else:
    raise SystemExit("Cleanup was not blocked by the active checkpoint.")
'@
    $checkpointProbe | & python -
    if ($LASTEXITCODE -ne 0) { throw "The Windows Broker did not expose and protect the active checkpoint summary." }
    if (-not (Test-Path -LiteralPath $checkpointPatch -PathType Leaf) -or
        -not (Test-Path -LiteralPath $checkpointMetadataPath -PathType Leaf)) {
        throw "Checkpoint bytes or metadata were modified during status/cleanup preflight."
    }

    # Deactivation must retain historical checkpoint bytes and allow safe cleanup.
    $checkpointIndex.active = $false
    [System.IO.File]::WriteAllText(
        $checkpointIndexPath,
        (ConvertTo-Json -InputObject $checkpointIndex -Depth 5 -Compress),
        [System.Text.UTF8Encoding]::new($false)
    )
    $inactiveProbe = @'
from app import autonomous_engineering_state_broker as broker
pending = broker.pending_work()
assert pending["available"] is True and pending["blocked"] is False
assert pending["checkpoint"] == {"available": False, "verified": True}
'@
    $inactiveProbe | & python -
    if ($LASTEXITCODE -ne 0) { throw "The Windows Broker did not honor the inactive checkpoint pointer." }

    # Cleanup must also fail while a watcher owns the store lock, even before
    # a new pointer is published.
    $checkpointLockPath = Join-Path $stateRoot "autonomous-checkpoint-store.lock"
    $checkpointLockStream = [System.IO.File]::Open(
        $checkpointLockPath,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::ReadWrite
    )
    try {
        if ($checkpointLockStream.Length -eq 0) {
            $checkpointLockStream.WriteByte(0)
            $checkpointLockStream.Flush($true)
        }
        $checkpointLockStream.Lock(0, 1)
        $writerLockProbe = @'
from app import autonomous_engineering_state_broker as broker
from app.autonomous_engineering_state_broker import StateBrokerConflict
try:
    broker.cleanup()
except StateBrokerConflict:
    pass
else:
    raise SystemExit("Cleanup ignored an active checkpoint writer lock.")
'@
        $writerLockProbe | & python -
        if ($LASTEXITCODE -ne 0) { throw "The Windows Broker ignored an active checkpoint writer lock." }
    } finally {
        try { $checkpointLockStream.Unlock(0, 1) } catch { }
        $checkpointLockStream.Dispose()
    }

    # A completed current run without either checkpoint file is an empty
    # checkpoint state, not a malformed checkpoint.
    Remove-Item -LiteralPath $checkpointIndexPath -Force
    $emptyCurrent = [ordered]@{ run_id = "77777"; run_attempt = "1"; status = "completed" }
    [System.IO.File]::WriteAllText(
        (Join-Path $stateRoot "current.json"),
        (ConvertTo-Json -InputObject $emptyCurrent -Depth 5 -Compress),
        [System.Text.UTF8Encoding]::new($false)
    )
    $emptyLegacyProbe = @'
from app import autonomous_engineering_state_broker as broker
pending = broker.pending_work()
assert pending["available"] is True and pending["blocked"] is False
assert pending["checkpoint"] == {"available": False, "verified": True}
'@
    $emptyLegacyProbe | & python -
    if ($LASTEXITCODE -ne 0) { throw "The Windows Broker misclassified a completed run with no checkpoint." }

    # Legacy checkpoints created before the atomic index are resolved only
    # through the current run identity; no directory scan or path endpoint exists.
    $legacyCurrent = [ordered]@{ run_id = $checkpointRunId; run_attempt = "1"; status = "completed" }
    [System.IO.File]::WriteAllText(
        (Join-Path $stateRoot "current.json"),
        (ConvertTo-Json -InputObject $legacyCurrent -Depth 5 -Compress),
        [System.Text.UTF8Encoding]::new($false)
    )
    $legacyProbe = @'
from app import autonomous_engineering_state_broker as broker
from app.autonomous_engineering_state_broker import StateBrokerConflict

pending = broker.pending_work()
assert pending["blocked"] is True
assert pending["checkpoint"]["available"] is True
assert pending["checkpoint"]["verified"] is True
assert pending["checkpoint"]["run_id"] == "12345"
try:
    broker.cleanup()
except StateBrokerConflict:
    pass
else:
    raise SystemExit("Legacy checkpoint was not protected.")
'@
    $legacyProbe | & python -
    if ($LASTEXITCODE -ne 0) { throw "The Windows Broker did not resolve a legacy checkpoint from the current run identity." }

    [System.IO.File]::WriteAllText(
        $checkpointIndexPath,
        (ConvertTo-Json -InputObject $checkpointIndex -Depth 5 -Compress),
        [System.Text.UTF8Encoding]::new($false)
    )

    $pendingMarker = Join-Path $stateRoot "pending-manual-task.txt"
    [System.IO.File]::WriteAllText($pendingMarker, "preserve-during-cleanup-test")
    $conflictProbe = @'
from app import autonomous_engineering_state_broker as broker
from app.autonomous_engineering_state_broker import StateBrokerConflict
try:
    broker.cleanup()
except StateBrokerConflict:
    pass
else:
    raise SystemExit("Cleanup was not blocked by the durable pending-task marker.")
'@
    $conflictProbe | & python -
    if ($LASTEXITCODE -ne 0) { throw "The broker did not fail closed when durable task state was pending." }
    if (-not (Test-Path -LiteralPath $pendingMarker -PathType Leaf)) {
        throw "The cleanup conflict test lost the durable pending-task marker."
    }

    Remove-Item -LiteralPath $pendingMarker -Force
    $cleanupProbe = @'
from app import autonomous_engineering_state_broker as broker
result = broker.cleanup()
assert result["conflict"] is False
assert result["cleaned"] is True
assert result["files_removed"] >= 2
'@
    $cleanupProbe | & python -
    if ($LASTEXITCODE -ne 0) { throw "The broker cleanup endpoint failed its live Windows integration test." }
    if (Test-Path -LiteralPath (Join-Path $stateRoot "current.json")) {
        throw "The broker cleanup endpoint did not remove the disposable current progress snapshot."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $stateRoot "history.jsonl")) -or
        -not (Test-Path -LiteralPath (Join-Path $stateRoot "scorecard-baseline.json"))) {
        throw "The broker cleanup endpoint did not preserve durable history and baseline data."
    }
    if (-not (Test-Path -LiteralPath $checkpointPatch -PathType Leaf) -or
        -not (Test-Path -LiteralPath $checkpointMetadataPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $checkpointIndexPath -PathType Leaf)) {
        throw "The Broker cleanup endpoint removed retained checkpoint data."
    }

    # Every worker must be able to create its own pipe instance. A worker
    # exception here is evidence of an ACL or lifecycle defect even if one
    # surviving worker happened to satisfy the sequential client probes.
    $workerDiagnosticsPath = Join-Path $stateRoot "service-errors.log"
    if (Test-Path -LiteralPath $workerDiagnosticsPath -PathType Leaf) {
        $workerDiagnostics = Get-Content -LiteralPath $workerDiagnosticsPath -Raw
        if (-not [String]::IsNullOrWhiteSpace($workerDiagnostics)) {
            throw "The state broker worker loop recorded unexpected exceptions: $workerDiagnostics"
        }
    }

    Stop-Service -Name $serviceName -Force -ErrorAction Stop
    (Get-Service -Name $serviceName -ErrorAction Stop).WaitForStatus(
        [System.ServiceProcess.ServiceControllerStatus]::Stopped,
        [TimeSpan]::FromSeconds(20)
    )
    $fakeServer = [System.IO.Pipes.NamedPipeServerStream]::new(
        "PersonalAIAssistant.StateBroker.v1",
        [System.IO.Pipes.PipeDirection]::InOut,
        1,
        [System.IO.Pipes.PipeTransmissionMode]::Byte,
        [System.IO.Pipes.PipeOptions]::Asynchronous
    )
    $acceptTask = $fakeServer.WaitForConnectionAsync()
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $timeoutObserved = $false
        try {
            Invoke-AutonomousStateBroker -Method GET -Path "/v1/health" -TimeoutMilliseconds 500 | Out-Null
        } catch {
            if ($_.Exception.Message -like "*timed out*") { $timeoutObserved = $true } else { throw }
        }
        if (-not $timeoutObserved) { throw "The PowerShell client did not time out when a connected peer withheld its response." }
        if ($stopwatch.Elapsed.TotalSeconds -gt 3) {
            throw "The PowerShell client exceeded its bounded response timeout."
        }
    } finally {
        $fakeServer.Dispose()
    }

    Write-Host "Live LocalService broker, Python overlapped pipe client, progress, diagnostics, pending-work conflict, safe cleanup, and PowerShell timeout tests passed."
} finally {
    $running = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    if ($running -and $running.Status -eq "Running") {
        Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
    }
    if ($serviceCreated -or (Get-Service -Name $serviceName -ErrorAction SilentlyContinue)) {
        sc.exe delete $serviceName | Out-Null
        Start-Sleep -Seconds 1
    }
    [Environment]::SetEnvironmentVariable(
        "AUTONOMOUS_ENGINEERING_PROGRESS_DIR",
        $oldMachineStatePath,
        "Machine"
    )
    [Environment]::SetEnvironmentVariable(
        "AUTONOMOUS_STATE_BROKER_DIAGNOSTICS",
        $oldDiagnosticsSetting,
        "Machine"
    )
    if (Test-Path -LiteralPath $stateRoot) {
        & icacls.exe $stateRoot /grant:r "*$($currentSid):(OI)(CI)F" /T /C /Q | Out-Null
        Remove-Item -LiteralPath $stateRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $serviceRoot) {
        & icacls.exe $serviceRoot /grant:r "*$($currentSid):(OI)(CI)F" /T /C /Q | Out-Null
        Remove-Item -LiteralPath $serviceRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
