Set-StrictMode -Version Latest

function Get-AutonomousTaskDigest {
    param([Parameter(Mandatory=$true)][string]$Task)
    $shaProvider = [System.Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString(
            $shaProvider.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Task))
        ).Replace("-", "").ToLowerInvariant()
    } finally {
        $shaProvider.Dispose()
    }
}

function Read-AutonomousTaskOwnerRecord {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$Task
    )
    try {
        $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    } catch [System.IO.FileNotFoundException] {
        return $null
    } catch [System.IO.DirectoryNotFoundException] {
        return $null
    } catch {
        throw "Pending task owner metadata cannot be read safely. $($_.Exception.Message)"
    }
    if ([string]::IsNullOrWhiteSpace($raw)) {
        throw "Pending task owner metadata is empty."
    }
    try {
        $record = $raw | ConvertFrom-Json
    } catch {
        throw "Pending task owner metadata is malformed. $($_.Exception.Message)"
    }

    $ownerAttempt = 0
    $submittedAttempt = 0
    $claimCount = 0
    $createdAt = 0.0
    $claimedAt = 0.0
    $taskDigest = Get-AutonomousTaskDigest -Task $Task
    if (
        $record.schema_version -is [bool] -or
        -not ($record.schema_version -is [int] -or $record.schema_version -is [long]) -or
        [long]$record.schema_version -ne 1 -or
        -not ($record.task_sha256 -is [string]) -or
        [string]$record.task_sha256 -cne $taskDigest -or
        -not ($record.owner_run_id -is [string]) -or
        [string]::IsNullOrWhiteSpace([string]$record.owner_run_id) -or
        -not ($record.submitted_run_id -is [string]) -or
        [string]::IsNullOrWhiteSpace([string]$record.submitted_run_id) -or
        -not ($record.owner_attempt -is [int] -or $record.owner_attempt -is [long]) -or
        -not [int]::TryParse([string]$record.owner_attempt, [ref]$ownerAttempt) -or
        $ownerAttempt -lt 1 -or
        -not ($record.submitted_attempt -is [int] -or $record.submitted_attempt -is [long]) -or
        -not [int]::TryParse([string]$record.submitted_attempt, [ref]$submittedAttempt) -or
        $submittedAttempt -lt 1 -or
        -not ($record.claim_count -is [int] -or $record.claim_count -is [long]) -or
        -not [int]::TryParse([string]$record.claim_count, [ref]$claimCount) -or
        $claimCount -lt 1 -or
        -not ($record.created_at -is [double] -or $record.created_at -is [int] -or $record.created_at -is [long]) -or
        -not [double]::TryParse([string]$record.created_at, [ref]$createdAt) -or
        [double]::IsNaN($createdAt) -or [double]::IsInfinity($createdAt) -or $createdAt -le 0 -or
        -not ($record.claimed_at -is [double] -or $record.claimed_at -is [int] -or $record.claimed_at -is [long]) -or
        -not [double]::TryParse([string]$record.claimed_at, [ref]$claimedAt) -or
        [double]::IsNaN($claimedAt) -or [double]::IsInfinity($claimedAt) -or $claimedAt -le 0 -or
        -not ($record.source -is [string]) -or
        [string]$record.source -notin @("manual_dispatch", "recovery", "legacy_migration")
    ) {
        throw "Pending task owner metadata violates schema v1 or does not match the pending task."
    }
    return $record
}

function Recover-AutonomousPendingStateCleanup {
    param([Parameter(Mandatory=$true)][string]$StateDirectory)
    $markerPath = Join-Path $StateDirectory "pending-state-cleanup.json"
    try {
        $raw = [System.IO.File]::ReadAllText($markerPath, [System.Text.Encoding]::UTF8)
    } catch [System.IO.FileNotFoundException] {
        return $false
    } catch [System.IO.DirectoryNotFoundException] {
        return $false
    } catch {
        throw "Pending-state cleanup marker cannot be read safely. $($_.Exception.Message)"
    }
    if ([string]::IsNullOrWhiteSpace($raw)) {
        throw "Pending-state cleanup marker is empty; preserving all durable state."
    }
    try {
        $marker = $raw | ConvertFrom-Json
    } catch {
        throw "Pending-state cleanup marker is malformed; preserving all durable state."
    }
    $createdAt = 0.0
    if (
        $marker.schema_version -is [bool] -or
        -not ($marker.schema_version -is [int] -or $marker.schema_version -is [long]) -or
        [long]$marker.schema_version -ne 1 -or
        [string]$marker.operation -cne "clear_after_verified_post_merge" -or
        -not ($marker.run_id -is [string]) -or
        [string]::IsNullOrWhiteSpace([string]$marker.run_id) -or
        -not ($marker.created_at -is [double] -or $marker.created_at -is [int] -or $marker.created_at -is [long]) -or
        -not [double]::TryParse([string]$marker.created_at, [ref]$createdAt) -or
        [double]::IsNaN($createdAt) -or [double]::IsInfinity($createdAt) -or $createdAt -le 0
    ) {
        throw "Pending-state cleanup marker violates schema v1; preserving all durable state."
    }

    foreach ($name in @(
        "pending-autonomous-pr.json",
        "pending-manual-task.txt",
        "pending-manual-task-owner.json"
    )) {
        $path = Join-Path $StateDirectory $name
        if (Test-Path -LiteralPath $path) {
            $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
            if ($item.PSIsContainer) {
                throw "Pending-state path '$name' is a directory; preserving the cleanup marker."
            }
            Remove-Item -LiteralPath $path -Force -ErrorAction Stop
        }
    }
    Remove-Item -LiteralPath $markerPath -Force -ErrorAction Stop
    return $true
}

function Write-AutonomousTaskOwnerRecord {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$Task,
        [Parameter(Mandatory=$true)][string]$RunId,
        [Parameter(Mandatory=$true)][int]$RunAttempt,
        [Parameter(Mandatory=$true)][ValidateSet("manual_dispatch", "recovery", "legacy_migration")][string]$Source,
        [AllowNull()][object]$Existing
    )
    if ([string]::IsNullOrWhiteSpace($Task) -or $Task.Length -gt 2000 -or $Task.IndexOf([char]0) -ge 0) {
        throw "Pending task violates the task text contract."
    }
    if ($RunId -notmatch '^[A-Za-z0-9_.-]{1,128}$' -or $RunAttempt -lt 1) {
        throw "A valid workflow run ID and positive attempt are required."
    }
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    if ($null -eq $Existing) {
        $record = [ordered]@{
            schema_version = 1
            task_sha256 = Get-AutonomousTaskDigest -Task $Task
            submitted_run_id = $RunId
            submitted_attempt = $RunAttempt
            owner_run_id = $RunId
            owner_attempt = $RunAttempt
            created_at = $now
            claimed_at = $now
            claim_count = 1
            source = $Source
        }
    } else {
        $record = $Existing
        if (
            [string]$record.owner_run_id -ne $RunId -or
            [int]$record.owner_attempt -ne $RunAttempt
        ) {
            $record.owner_run_id = $RunId
            $record.owner_attempt = $RunAttempt
            $record.claimed_at = $now
            $record.claim_count = [int]$record.claim_count + 1
        }
    }

    $directory = Split-Path -Parent $Path
    $temporary = Join-Path $directory ("pending-manual-task-owner-" + $RunId + "-" + $RunAttempt + ".tmp")
    try {
        $json = $record | ConvertTo-Json -Depth 5 -Compress
        [System.IO.File]::WriteAllText(
            $temporary, $json, [System.Text.UTF8Encoding]::new($false)
        )
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    } finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
    return $record
}

Export-ModuleMember -Function Get-AutonomousTaskDigest, Read-AutonomousTaskOwnerRecord, Write-AutonomousTaskOwnerRecord, Recover-AutonomousPendingStateCleanup
