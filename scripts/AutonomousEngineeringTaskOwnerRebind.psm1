Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot "AutonomousEngineeringTaskState.psm1") -Force

function Rebind-AutonomousTaskOwnerRecord {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$Task,
        [Parameter(Mandatory=$true)][string]$RunId,
        [Parameter(Mandatory=$true)][int]$RunAttempt,
        [Parameter(Mandatory=$true)][object]$VerifiedOwnerRun
    )

    if ([string]::IsNullOrWhiteSpace($Task) -or $Task.Length -gt 2000 -or $Task.IndexOf([char]0) -ge 0) {
        throw "Pending task violates the task text contract."
    }
    if ($RunId -notmatch '^[0-9]{1,20}
    if (
        [string]$VerifiedOwnerRun.id -notmatch '^[0-9]{1,20}
        [string]$VerifiedOwnerRun.path -cne ".github/workflows/autonomous-codex-engineering.yml" -or
        [string]$VerifiedOwnerRun.head_branch -cne "main" -or
        [string]$VerifiedOwnerRun.status -cne "completed" -or
        [string]$VerifiedOwnerRun.conclusion -notin @("failure", "cancelled", "timed_out")
    ) {
        throw "The previous owner run is not verified as a terminal unsuccessful main-branch execution."
    }

    try {
        $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
        $existing = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Pending task owner metadata cannot be read safely; preserving all state."
    }

    $ownerAttempt = 0
    $submittedAttempt = 0
    $claimCount = 0
    $createdAt = 0.0
    $claimedAt = 0.0
    $oldDigest = [string]$existing.task_sha256
    if (
        $existing.schema_version -is [bool] -or
        -not ($existing.schema_version -is [int] -or $existing.schema_version -is [long]) -or
        [long]$existing.schema_version -ne 1 -or
        $oldDigest -notmatch '^[0-9a-f]{64}$' -or
        [string]$existing.owner_run_id -cne [string]$VerifiedOwnerRun.id -or
        [string]$existing.owner_run_id -notmatch '^[0-9]{1,20}
        -not [int]::TryParse([string]$existing.owner_attempt, [ref]$ownerAttempt) -or $ownerAttempt -lt 1 -or
        -not [int]::TryParse([string]$existing.submitted_attempt, [ref]$submittedAttempt) -or $submittedAttempt -lt 1 -or
        [string]$existing.submitted_run_id -notmatch '^[0-9]{1,20}
        -not [int]::TryParse([string]$existing.claim_count, [ref]$claimCount) -or $claimCount -lt 1 -or
        -not [double]::TryParse([string]$existing.created_at, [ref]$createdAt) -or
        [double]::IsNaN($createdAt) -or [double]::IsInfinity($createdAt) -or $createdAt -le 0 -or
        -not [double]::TryParse([string]$existing.claimed_at, [ref]$claimedAt) -or
        [double]::IsNaN($claimedAt) -or [double]::IsInfinity($claimedAt) -or $claimedAt -le 0 -or
        [string]$existing.source -notin @("manual_dispatch", "recovery", "legacy_migration")
    ) {
        throw "Pending task owner metadata is not structurally valid schema v1; preserving it."
    }

    $newDigest = Get-AutonomousTaskDigest -Task $Task
    $rebindReason = if ($oldDigest -ceq $newDigest) {
        "owner_authorized_failed_run_recovery"
    } else {
        "owner_authorized_task_content_reconciliation"
    }

    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $record = [ordered]@{
        schema_version = 2
        task_sha256 = $newDigest
        submitted_run_id = [string]$existing.submitted_run_id
        submitted_attempt = $submittedAttempt
        owner_run_id = $RunId
        owner_attempt = $RunAttempt
        created_at = $createdAt
        claimed_at = $now
        claim_count = $claimCount + 1
        source = "recovery"
        previous_task_sha256 = $oldDigest
        rebind_reason = $rebindReason
        rebind_authorization_run_id = $RunId
        rebound_at = $now
    }

    $nonce = [guid]::NewGuid().ToString('N')
    $backupPath = "$Path.quarantine-rebound-$RunId-$RunAttempt-$nonce.json"
    $temporaryPath = "$Path.$RunId-$RunAttempt-$nonce.rebind.tmp"
    try {
        $json = $record | ConvertTo-Json -Depth 5 -Compress
        $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($json)
        $stream = [System.IO.File]::Open(
            $temporaryPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        try {
            $stream.Write($bytes, 0, $bytes.Length)
            $stream.Flush($true)
        } finally {
            $stream.Dispose()
        }

        # Validate the exact bytes that will be committed before replacing the
        # live owner record. A serialization/schema defect must leave it intact.
        $null = Get-AutonomousTaskOwnerRecord -Path $temporaryPath -Task $Task
        [System.IO.File]::Replace($temporaryPath, $Path, $backupPath, $true)
    } catch {
        throw "Explicit owner reconciliation failed; the original owner record remains preserved. $($_.Exception.Message)"
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }

    return [pscustomobject]$record
}

Export-ModuleMember -Function Rebind-AutonomousTaskOwnerRecord
 -or $RunAttempt -lt 1) {
        throw "A numeric current workflow run ID and positive attempt are required."
    }
    if (
        [string]$VerifiedOwnerRun.id -notmatch '^[0-9]+$' -or
        [string]$VerifiedOwnerRun.path -cne ".github/workflows/autonomous-codex-engineering.yml" -or
        [string]$VerifiedOwnerRun.head_branch -cne "main" -or
        [string]$VerifiedOwnerRun.status -cne "completed" -or
        [string]$VerifiedOwnerRun.conclusion -notin @("failure", "cancelled", "timed_out")
    ) {
        throw "The previous owner run is not verified as a terminal unsuccessful main-branch execution."
    }

    try {
        $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
        $existing = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Pending task owner metadata cannot be read safely; preserving all state."
    }

    $ownerAttempt = 0
    $submittedAttempt = 0
    $claimCount = 0
    $createdAt = 0.0
    $claimedAt = 0.0
    $oldDigest = [string]$existing.task_sha256
    if (
        $existing.schema_version -is [bool] -or
        -not ($existing.schema_version -is [int] -or $existing.schema_version -is [long]) -or
        [long]$existing.schema_version -ne 1 -or
        $oldDigest -notmatch '^[0-9a-f]{64}$' -or
        [string]$existing.owner_run_id -cne [string]$VerifiedOwnerRun.id -or
        [string]$existing.owner_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.owner_attempt, [ref]$ownerAttempt) -or $ownerAttempt -lt 1 -or
        -not [int]::TryParse([string]$existing.submitted_attempt, [ref]$submittedAttempt) -or $submittedAttempt -lt 1 -or
        [string]$existing.submitted_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.claim_count, [ref]$claimCount) -or $claimCount -lt 1 -or
        -not [double]::TryParse([string]$existing.created_at, [ref]$createdAt) -or
        [double]::IsNaN($createdAt) -or [double]::IsInfinity($createdAt) -or $createdAt -le 0 -or
        -not [double]::TryParse([string]$existing.claimed_at, [ref]$claimedAt) -or
        [double]::IsNaN($claimedAt) -or [double]::IsInfinity($claimedAt) -or $claimedAt -le 0 -or
        [string]$existing.source -notin @("manual_dispatch", "recovery", "legacy_migration")
    ) {
        throw "Pending task owner metadata is not structurally valid schema v1; preserving it."
    }

    $newDigest = Get-AutonomousTaskDigest -Task $Task
    if ($oldDigest -ceq $newDigest) {
        throw "Pending task owner digest already matches; explicit rebind is unnecessary."
    }

    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $record = [ordered]@{
        schema_version = 2
        task_sha256 = $newDigest
        submitted_run_id = [string]$existing.submitted_run_id
        submitted_attempt = $submittedAttempt
        owner_run_id = $RunId
        owner_attempt = $RunAttempt
        created_at = $createdAt
        claimed_at = $now
        claim_count = $claimCount + 1
        source = "recovery"
        previous_task_sha256 = $oldDigest
        rebind_reason = "owner_authorized_task_content_reconciliation"
        rebind_authorization_run_id = $RunId
        rebound_at = $now
    }

    $nonce = [guid]::NewGuid().ToString('N')
    $backupPath = "$Path.quarantine-rebound-$RunId-$RunAttempt-$nonce.json"
    $temporaryPath = "$Path.$RunId-$RunAttempt-$nonce.rebind.tmp"
    try {
        [System.IO.File]::WriteAllText(
            $temporaryPath,
            ($record | ConvertTo-Json -Depth 5 -Compress),
            [System.Text.UTF8Encoding]::new($false)
        )
        [System.IO.File]::Replace($temporaryPath, $Path, $backupPath, $true)
    } catch {
        throw "Explicit owner reconciliation failed; the original owner record remains preserved. $($_.Exception.Message)"
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }

    return [pscustomobject]$record
}

Export-ModuleMember -Function Rebind-AutonomousTaskOwnerRecord
 -or
        [string]$VerifiedOwnerRun.path -cne ".github/workflows/autonomous-codex-engineering.yml" -or
        [string]$VerifiedOwnerRun.head_branch -cne "main" -or
        [string]$VerifiedOwnerRun.status -cne "completed" -or
        [string]$VerifiedOwnerRun.conclusion -notin @("failure", "cancelled", "timed_out")
    ) {
        throw "The previous owner run is not verified as a terminal unsuccessful main-branch execution."
    }

    try {
        $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
        $existing = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Pending task owner metadata cannot be read safely; preserving all state."
    }

    $ownerAttempt = 0
    $submittedAttempt = 0
    $claimCount = 0
    $createdAt = 0.0
    $claimedAt = 0.0
    $oldDigest = [string]$existing.task_sha256
    if (
        $existing.schema_version -is [bool] -or
        -not ($existing.schema_version -is [int] -or $existing.schema_version -is [long]) -or
        [long]$existing.schema_version -ne 1 -or
        $oldDigest -notmatch '^[0-9a-f]{64}$' -or
        [string]$existing.owner_run_id -cne [string]$VerifiedOwnerRun.id -or
        [string]$existing.owner_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.owner_attempt, [ref]$ownerAttempt) -or $ownerAttempt -lt 1 -or
        -not [int]::TryParse([string]$existing.submitted_attempt, [ref]$submittedAttempt) -or $submittedAttempt -lt 1 -or
        [string]$existing.submitted_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.claim_count, [ref]$claimCount) -or $claimCount -lt 1 -or
        -not [double]::TryParse([string]$existing.created_at, [ref]$createdAt) -or
        [double]::IsNaN($createdAt) -or [double]::IsInfinity($createdAt) -or $createdAt -le 0 -or
        -not [double]::TryParse([string]$existing.claimed_at, [ref]$claimedAt) -or
        [double]::IsNaN($claimedAt) -or [double]::IsInfinity($claimedAt) -or $claimedAt -le 0 -or
        [string]$existing.source -notin @("manual_dispatch", "recovery", "legacy_migration")
    ) {
        throw "Pending task owner metadata is not structurally valid schema v1; preserving it."
    }

    $newDigest = Get-AutonomousTaskDigest -Task $Task
    if ($oldDigest -ceq $newDigest) {
        throw "Pending task owner digest already matches; explicit rebind is unnecessary."
    }

    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $record = [ordered]@{
        schema_version = 2
        task_sha256 = $newDigest
        submitted_run_id = [string]$existing.submitted_run_id
        submitted_attempt = $submittedAttempt
        owner_run_id = $RunId
        owner_attempt = $RunAttempt
        created_at = $createdAt
        claimed_at = $now
        claim_count = $claimCount + 1
        source = "recovery"
        previous_task_sha256 = $oldDigest
        rebind_reason = "owner_authorized_task_content_reconciliation"
        rebind_authorization_run_id = $RunId
        rebound_at = $now
    }

    $nonce = [guid]::NewGuid().ToString('N')
    $backupPath = "$Path.quarantine-rebound-$RunId-$RunAttempt-$nonce.json"
    $temporaryPath = "$Path.$RunId-$RunAttempt-$nonce.rebind.tmp"
    try {
        [System.IO.File]::WriteAllText(
            $temporaryPath,
            ($record | ConvertTo-Json -Depth 5 -Compress),
            [System.Text.UTF8Encoding]::new($false)
        )
        [System.IO.File]::Replace($temporaryPath, $Path, $backupPath, $true)
    } catch {
        throw "Explicit owner reconciliation failed; the original owner record remains preserved. $($_.Exception.Message)"
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }

    return [pscustomobject]$record
}

Export-ModuleMember -Function Rebind-AutonomousTaskOwnerRecord
 -or $RunAttempt -lt 1) {
        throw "A numeric current workflow run ID and positive attempt are required."
    }
    if (
        [string]$VerifiedOwnerRun.id -notmatch '^[0-9]+$' -or
        [string]$VerifiedOwnerRun.path -cne ".github/workflows/autonomous-codex-engineering.yml" -or
        [string]$VerifiedOwnerRun.head_branch -cne "main" -or
        [string]$VerifiedOwnerRun.status -cne "completed" -or
        [string]$VerifiedOwnerRun.conclusion -notin @("failure", "cancelled", "timed_out")
    ) {
        throw "The previous owner run is not verified as a terminal unsuccessful main-branch execution."
    }

    try {
        $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
        $existing = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Pending task owner metadata cannot be read safely; preserving all state."
    }

    $ownerAttempt = 0
    $submittedAttempt = 0
    $claimCount = 0
    $createdAt = 0.0
    $claimedAt = 0.0
    $oldDigest = [string]$existing.task_sha256
    if (
        $existing.schema_version -is [bool] -or
        -not ($existing.schema_version -is [int] -or $existing.schema_version -is [long]) -or
        [long]$existing.schema_version -ne 1 -or
        $oldDigest -notmatch '^[0-9a-f]{64}$' -or
        [string]$existing.owner_run_id -cne [string]$VerifiedOwnerRun.id -or
        [string]$existing.owner_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.owner_attempt, [ref]$ownerAttempt) -or $ownerAttempt -lt 1 -or
        -not [int]::TryParse([string]$existing.submitted_attempt, [ref]$submittedAttempt) -or $submittedAttempt -lt 1 -or
        [string]$existing.submitted_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.claim_count, [ref]$claimCount) -or $claimCount -lt 1 -or
        -not [double]::TryParse([string]$existing.created_at, [ref]$createdAt) -or
        [double]::IsNaN($createdAt) -or [double]::IsInfinity($createdAt) -or $createdAt -le 0 -or
        -not [double]::TryParse([string]$existing.claimed_at, [ref]$claimedAt) -or
        [double]::IsNaN($claimedAt) -or [double]::IsInfinity($claimedAt) -or $claimedAt -le 0 -or
        [string]$existing.source -notin @("manual_dispatch", "recovery", "legacy_migration")
    ) {
        throw "Pending task owner metadata is not structurally valid schema v1; preserving it."
    }

    $newDigest = Get-AutonomousTaskDigest -Task $Task
    if ($oldDigest -ceq $newDigest) {
        throw "Pending task owner digest already matches; explicit rebind is unnecessary."
    }

    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $record = [ordered]@{
        schema_version = 2
        task_sha256 = $newDigest
        submitted_run_id = [string]$existing.submitted_run_id
        submitted_attempt = $submittedAttempt
        owner_run_id = $RunId
        owner_attempt = $RunAttempt
        created_at = $createdAt
        claimed_at = $now
        claim_count = $claimCount + 1
        source = "recovery"
        previous_task_sha256 = $oldDigest
        rebind_reason = "owner_authorized_task_content_reconciliation"
        rebind_authorization_run_id = $RunId
        rebound_at = $now
    }

    $nonce = [guid]::NewGuid().ToString('N')
    $backupPath = "$Path.quarantine-rebound-$RunId-$RunAttempt-$nonce.json"
    $temporaryPath = "$Path.$RunId-$RunAttempt-$nonce.rebind.tmp"
    try {
        [System.IO.File]::WriteAllText(
            $temporaryPath,
            ($record | ConvertTo-Json -Depth 5 -Compress),
            [System.Text.UTF8Encoding]::new($false)
        )
        [System.IO.File]::Replace($temporaryPath, $Path, $backupPath, $true)
    } catch {
        throw "Explicit owner reconciliation failed; the original owner record remains preserved. $($_.Exception.Message)"
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }

    return [pscustomobject]$record
}

Export-ModuleMember -Function Rebind-AutonomousTaskOwnerRecord
 -or
        -not [int]::TryParse([string]$existing.owner_attempt, [ref]$ownerAttempt) -or $ownerAttempt -lt 1 -or
        -not [int]::TryParse([string]$existing.submitted_attempt, [ref]$submittedAttempt) -or $submittedAttempt -lt 1 -or
        [string]$existing.submitted_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.claim_count, [ref]$claimCount) -or $claimCount -lt 1 -or
        -not [double]::TryParse([string]$existing.created_at, [ref]$createdAt) -or
        [double]::IsNaN($createdAt) -or [double]::IsInfinity($createdAt) -or $createdAt -le 0 -or
        -not [double]::TryParse([string]$existing.claimed_at, [ref]$claimedAt) -or
        [double]::IsNaN($claimedAt) -or [double]::IsInfinity($claimedAt) -or $claimedAt -le 0 -or
        [string]$existing.source -notin @("manual_dispatch", "recovery", "legacy_migration")
    ) {
        throw "Pending task owner metadata is not structurally valid schema v1; preserving it."
    }

    $newDigest = Get-AutonomousTaskDigest -Task $Task
    if ($oldDigest -ceq $newDigest) {
        throw "Pending task owner digest already matches; explicit rebind is unnecessary."
    }

    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $record = [ordered]@{
        schema_version = 2
        task_sha256 = $newDigest
        submitted_run_id = [string]$existing.submitted_run_id
        submitted_attempt = $submittedAttempt
        owner_run_id = $RunId
        owner_attempt = $RunAttempt
        created_at = $createdAt
        claimed_at = $now
        claim_count = $claimCount + 1
        source = "recovery"
        previous_task_sha256 = $oldDigest
        rebind_reason = "owner_authorized_task_content_reconciliation"
        rebind_authorization_run_id = $RunId
        rebound_at = $now
    }

    $nonce = [guid]::NewGuid().ToString('N')
    $backupPath = "$Path.quarantine-rebound-$RunId-$RunAttempt-$nonce.json"
    $temporaryPath = "$Path.$RunId-$RunAttempt-$nonce.rebind.tmp"
    try {
        [System.IO.File]::WriteAllText(
            $temporaryPath,
            ($record | ConvertTo-Json -Depth 5 -Compress),
            [System.Text.UTF8Encoding]::new($false)
        )
        [System.IO.File]::Replace($temporaryPath, $Path, $backupPath, $true)
    } catch {
        throw "Explicit owner reconciliation failed; the original owner record remains preserved. $($_.Exception.Message)"
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }

    return [pscustomobject]$record
}

Export-ModuleMember -Function Rebind-AutonomousTaskOwnerRecord
 -or $RunAttempt -lt 1) {
        throw "A numeric current workflow run ID and positive attempt are required."
    }
    if (
        [string]$VerifiedOwnerRun.id -notmatch '^[0-9]+$' -or
        [string]$VerifiedOwnerRun.path -cne ".github/workflows/autonomous-codex-engineering.yml" -or
        [string]$VerifiedOwnerRun.head_branch -cne "main" -or
        [string]$VerifiedOwnerRun.status -cne "completed" -or
        [string]$VerifiedOwnerRun.conclusion -notin @("failure", "cancelled", "timed_out")
    ) {
        throw "The previous owner run is not verified as a terminal unsuccessful main-branch execution."
    }

    try {
        $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
        $existing = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Pending task owner metadata cannot be read safely; preserving all state."
    }

    $ownerAttempt = 0
    $submittedAttempt = 0
    $claimCount = 0
    $createdAt = 0.0
    $claimedAt = 0.0
    $oldDigest = [string]$existing.task_sha256
    if (
        $existing.schema_version -is [bool] -or
        -not ($existing.schema_version -is [int] -or $existing.schema_version -is [long]) -or
        [long]$existing.schema_version -ne 1 -or
        $oldDigest -notmatch '^[0-9a-f]{64}$' -or
        [string]$existing.owner_run_id -cne [string]$VerifiedOwnerRun.id -or
        [string]$existing.owner_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.owner_attempt, [ref]$ownerAttempt) -or $ownerAttempt -lt 1 -or
        -not [int]::TryParse([string]$existing.submitted_attempt, [ref]$submittedAttempt) -or $submittedAttempt -lt 1 -or
        [string]$existing.submitted_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.claim_count, [ref]$claimCount) -or $claimCount -lt 1 -or
        -not [double]::TryParse([string]$existing.created_at, [ref]$createdAt) -or
        [double]::IsNaN($createdAt) -or [double]::IsInfinity($createdAt) -or $createdAt -le 0 -or
        -not [double]::TryParse([string]$existing.claimed_at, [ref]$claimedAt) -or
        [double]::IsNaN($claimedAt) -or [double]::IsInfinity($claimedAt) -or $claimedAt -le 0 -or
        [string]$existing.source -notin @("manual_dispatch", "recovery", "legacy_migration")
    ) {
        throw "Pending task owner metadata is not structurally valid schema v1; preserving it."
    }

    $newDigest = Get-AutonomousTaskDigest -Task $Task
    if ($oldDigest -ceq $newDigest) {
        throw "Pending task owner digest already matches; explicit rebind is unnecessary."
    }

    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $record = [ordered]@{
        schema_version = 2
        task_sha256 = $newDigest
        submitted_run_id = [string]$existing.submitted_run_id
        submitted_attempt = $submittedAttempt
        owner_run_id = $RunId
        owner_attempt = $RunAttempt
        created_at = $createdAt
        claimed_at = $now
        claim_count = $claimCount + 1
        source = "recovery"
        previous_task_sha256 = $oldDigest
        rebind_reason = "owner_authorized_task_content_reconciliation"
        rebind_authorization_run_id = $RunId
        rebound_at = $now
    }

    $nonce = [guid]::NewGuid().ToString('N')
    $backupPath = "$Path.quarantine-rebound-$RunId-$RunAttempt-$nonce.json"
    $temporaryPath = "$Path.$RunId-$RunAttempt-$nonce.rebind.tmp"
    try {
        [System.IO.File]::WriteAllText(
            $temporaryPath,
            ($record | ConvertTo-Json -Depth 5 -Compress),
            [System.Text.UTF8Encoding]::new($false)
        )
        [System.IO.File]::Replace($temporaryPath, $Path, $backupPath, $true)
    } catch {
        throw "Explicit owner reconciliation failed; the original owner record remains preserved. $($_.Exception.Message)"
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }

    return [pscustomobject]$record
}

Export-ModuleMember -Function Rebind-AutonomousTaskOwnerRecord
 -or
        [string]$VerifiedOwnerRun.path -cne ".github/workflows/autonomous-codex-engineering.yml" -or
        [string]$VerifiedOwnerRun.head_branch -cne "main" -or
        [string]$VerifiedOwnerRun.status -cne "completed" -or
        [string]$VerifiedOwnerRun.conclusion -notin @("failure", "cancelled", "timed_out")
    ) {
        throw "The previous owner run is not verified as a terminal unsuccessful main-branch execution."
    }

    try {
        $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
        $existing = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Pending task owner metadata cannot be read safely; preserving all state."
    }

    $ownerAttempt = 0
    $submittedAttempt = 0
    $claimCount = 0
    $createdAt = 0.0
    $claimedAt = 0.0
    $oldDigest = [string]$existing.task_sha256
    if (
        $existing.schema_version -is [bool] -or
        -not ($existing.schema_version -is [int] -or $existing.schema_version -is [long]) -or
        [long]$existing.schema_version -ne 1 -or
        $oldDigest -notmatch '^[0-9a-f]{64}$' -or
        [string]$existing.owner_run_id -cne [string]$VerifiedOwnerRun.id -or
        [string]$existing.owner_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.owner_attempt, [ref]$ownerAttempt) -or $ownerAttempt -lt 1 -or
        -not [int]::TryParse([string]$existing.submitted_attempt, [ref]$submittedAttempt) -or $submittedAttempt -lt 1 -or
        [string]$existing.submitted_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.claim_count, [ref]$claimCount) -or $claimCount -lt 1 -or
        -not [double]::TryParse([string]$existing.created_at, [ref]$createdAt) -or
        [double]::IsNaN($createdAt) -or [double]::IsInfinity($createdAt) -or $createdAt -le 0 -or
        -not [double]::TryParse([string]$existing.claimed_at, [ref]$claimedAt) -or
        [double]::IsNaN($claimedAt) -or [double]::IsInfinity($claimedAt) -or $claimedAt -le 0 -or
        [string]$existing.source -notin @("manual_dispatch", "recovery", "legacy_migration")
    ) {
        throw "Pending task owner metadata is not structurally valid schema v1; preserving it."
    }

    $newDigest = Get-AutonomousTaskDigest -Task $Task
    if ($oldDigest -ceq $newDigest) {
        throw "Pending task owner digest already matches; explicit rebind is unnecessary."
    }

    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $record = [ordered]@{
        schema_version = 2
        task_sha256 = $newDigest
        submitted_run_id = [string]$existing.submitted_run_id
        submitted_attempt = $submittedAttempt
        owner_run_id = $RunId
        owner_attempt = $RunAttempt
        created_at = $createdAt
        claimed_at = $now
        claim_count = $claimCount + 1
        source = "recovery"
        previous_task_sha256 = $oldDigest
        rebind_reason = "owner_authorized_task_content_reconciliation"
        rebind_authorization_run_id = $RunId
        rebound_at = $now
    }

    $nonce = [guid]::NewGuid().ToString('N')
    $backupPath = "$Path.quarantine-rebound-$RunId-$RunAttempt-$nonce.json"
    $temporaryPath = "$Path.$RunId-$RunAttempt-$nonce.rebind.tmp"
    try {
        [System.IO.File]::WriteAllText(
            $temporaryPath,
            ($record | ConvertTo-Json -Depth 5 -Compress),
            [System.Text.UTF8Encoding]::new($false)
        )
        [System.IO.File]::Replace($temporaryPath, $Path, $backupPath, $true)
    } catch {
        throw "Explicit owner reconciliation failed; the original owner record remains preserved. $($_.Exception.Message)"
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }

    return [pscustomobject]$record
}

Export-ModuleMember -Function Rebind-AutonomousTaskOwnerRecord
 -or $RunAttempt -lt 1) {
        throw "A numeric current workflow run ID and positive attempt are required."
    }
    if (
        [string]$VerifiedOwnerRun.id -notmatch '^[0-9]+$' -or
        [string]$VerifiedOwnerRun.path -cne ".github/workflows/autonomous-codex-engineering.yml" -or
        [string]$VerifiedOwnerRun.head_branch -cne "main" -or
        [string]$VerifiedOwnerRun.status -cne "completed" -or
        [string]$VerifiedOwnerRun.conclusion -notin @("failure", "cancelled", "timed_out")
    ) {
        throw "The previous owner run is not verified as a terminal unsuccessful main-branch execution."
    }

    try {
        $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
        $existing = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Pending task owner metadata cannot be read safely; preserving all state."
    }

    $ownerAttempt = 0
    $submittedAttempt = 0
    $claimCount = 0
    $createdAt = 0.0
    $claimedAt = 0.0
    $oldDigest = [string]$existing.task_sha256
    if (
        $existing.schema_version -is [bool] -or
        -not ($existing.schema_version -is [int] -or $existing.schema_version -is [long]) -or
        [long]$existing.schema_version -ne 1 -or
        $oldDigest -notmatch '^[0-9a-f]{64}$' -or
        [string]$existing.owner_run_id -cne [string]$VerifiedOwnerRun.id -or
        [string]$existing.owner_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.owner_attempt, [ref]$ownerAttempt) -or $ownerAttempt -lt 1 -or
        -not [int]::TryParse([string]$existing.submitted_attempt, [ref]$submittedAttempt) -or $submittedAttempt -lt 1 -or
        [string]$existing.submitted_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.claim_count, [ref]$claimCount) -or $claimCount -lt 1 -or
        -not [double]::TryParse([string]$existing.created_at, [ref]$createdAt) -or
        [double]::IsNaN($createdAt) -or [double]::IsInfinity($createdAt) -or $createdAt -le 0 -or
        -not [double]::TryParse([string]$existing.claimed_at, [ref]$claimedAt) -or
        [double]::IsNaN($claimedAt) -or [double]::IsInfinity($claimedAt) -or $claimedAt -le 0 -or
        [string]$existing.source -notin @("manual_dispatch", "recovery", "legacy_migration")
    ) {
        throw "Pending task owner metadata is not structurally valid schema v1; preserving it."
    }

    $newDigest = Get-AutonomousTaskDigest -Task $Task
    if ($oldDigest -ceq $newDigest) {
        throw "Pending task owner digest already matches; explicit rebind is unnecessary."
    }

    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $record = [ordered]@{
        schema_version = 2
        task_sha256 = $newDigest
        submitted_run_id = [string]$existing.submitted_run_id
        submitted_attempt = $submittedAttempt
        owner_run_id = $RunId
        owner_attempt = $RunAttempt
        created_at = $createdAt
        claimed_at = $now
        claim_count = $claimCount + 1
        source = "recovery"
        previous_task_sha256 = $oldDigest
        rebind_reason = "owner_authorized_task_content_reconciliation"
        rebind_authorization_run_id = $RunId
        rebound_at = $now
    }

    $nonce = [guid]::NewGuid().ToString('N')
    $backupPath = "$Path.quarantine-rebound-$RunId-$RunAttempt-$nonce.json"
    $temporaryPath = "$Path.$RunId-$RunAttempt-$nonce.rebind.tmp"
    try {
        [System.IO.File]::WriteAllText(
            $temporaryPath,
            ($record | ConvertTo-Json -Depth 5 -Compress),
            [System.Text.UTF8Encoding]::new($false)
        )
        [System.IO.File]::Replace($temporaryPath, $Path, $backupPath, $true)
    } catch {
        throw "Explicit owner reconciliation failed; the original owner record remains preserved. $($_.Exception.Message)"
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }

    return [pscustomobject]$record
}

Export-ModuleMember -Function Rebind-AutonomousTaskOwnerRecord
 -or
        -not [int]::TryParse([string]$existing.claim_count, [ref]$claimCount) -or $claimCount -lt 1 -or
        -not [double]::TryParse([string]$existing.created_at, [ref]$createdAt) -or
        [double]::IsNaN($createdAt) -or [double]::IsInfinity($createdAt) -or $createdAt -le 0 -or
        -not [double]::TryParse([string]$existing.claimed_at, [ref]$claimedAt) -or
        [double]::IsNaN($claimedAt) -or [double]::IsInfinity($claimedAt) -or $claimedAt -le 0 -or
        [string]$existing.source -notin @("manual_dispatch", "recovery", "legacy_migration")
    ) {
        throw "Pending task owner metadata is not structurally valid schema v1; preserving it."
    }

    $newDigest = Get-AutonomousTaskDigest -Task $Task
    if ($oldDigest -ceq $newDigest) {
        throw "Pending task owner digest already matches; explicit rebind is unnecessary."
    }

    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $record = [ordered]@{
        schema_version = 2
        task_sha256 = $newDigest
        submitted_run_id = [string]$existing.submitted_run_id
        submitted_attempt = $submittedAttempt
        owner_run_id = $RunId
        owner_attempt = $RunAttempt
        created_at = $createdAt
        claimed_at = $now
        claim_count = $claimCount + 1
        source = "recovery"
        previous_task_sha256 = $oldDigest
        rebind_reason = "owner_authorized_task_content_reconciliation"
        rebind_authorization_run_id = $RunId
        rebound_at = $now
    }

    $nonce = [guid]::NewGuid().ToString('N')
    $backupPath = "$Path.quarantine-rebound-$RunId-$RunAttempt-$nonce.json"
    $temporaryPath = "$Path.$RunId-$RunAttempt-$nonce.rebind.tmp"
    try {
        [System.IO.File]::WriteAllText(
            $temporaryPath,
            ($record | ConvertTo-Json -Depth 5 -Compress),
            [System.Text.UTF8Encoding]::new($false)
        )
        [System.IO.File]::Replace($temporaryPath, $Path, $backupPath, $true)
    } catch {
        throw "Explicit owner reconciliation failed; the original owner record remains preserved. $($_.Exception.Message)"
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }

    return [pscustomobject]$record
}

Export-ModuleMember -Function Rebind-AutonomousTaskOwnerRecord
 -or $RunAttempt -lt 1) {
        throw "A numeric current workflow run ID and positive attempt are required."
    }
    if (
        [string]$VerifiedOwnerRun.id -notmatch '^[0-9]+$' -or
        [string]$VerifiedOwnerRun.path -cne ".github/workflows/autonomous-codex-engineering.yml" -or
        [string]$VerifiedOwnerRun.head_branch -cne "main" -or
        [string]$VerifiedOwnerRun.status -cne "completed" -or
        [string]$VerifiedOwnerRun.conclusion -notin @("failure", "cancelled", "timed_out")
    ) {
        throw "The previous owner run is not verified as a terminal unsuccessful main-branch execution."
    }

    try {
        $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
        $existing = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Pending task owner metadata cannot be read safely; preserving all state."
    }

    $ownerAttempt = 0
    $submittedAttempt = 0
    $claimCount = 0
    $createdAt = 0.0
    $claimedAt = 0.0
    $oldDigest = [string]$existing.task_sha256
    if (
        $existing.schema_version -is [bool] -or
        -not ($existing.schema_version -is [int] -or $existing.schema_version -is [long]) -or
        [long]$existing.schema_version -ne 1 -or
        $oldDigest -notmatch '^[0-9a-f]{64}$' -or
        [string]$existing.owner_run_id -cne [string]$VerifiedOwnerRun.id -or
        [string]$existing.owner_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.owner_attempt, [ref]$ownerAttempt) -or $ownerAttempt -lt 1 -or
        -not [int]::TryParse([string]$existing.submitted_attempt, [ref]$submittedAttempt) -or $submittedAttempt -lt 1 -or
        [string]$existing.submitted_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.claim_count, [ref]$claimCount) -or $claimCount -lt 1 -or
        -not [double]::TryParse([string]$existing.created_at, [ref]$createdAt) -or
        [double]::IsNaN($createdAt) -or [double]::IsInfinity($createdAt) -or $createdAt -le 0 -or
        -not [double]::TryParse([string]$existing.claimed_at, [ref]$claimedAt) -or
        [double]::IsNaN($claimedAt) -or [double]::IsInfinity($claimedAt) -or $claimedAt -le 0 -or
        [string]$existing.source -notin @("manual_dispatch", "recovery", "legacy_migration")
    ) {
        throw "Pending task owner metadata is not structurally valid schema v1; preserving it."
    }

    $newDigest = Get-AutonomousTaskDigest -Task $Task
    if ($oldDigest -ceq $newDigest) {
        throw "Pending task owner digest already matches; explicit rebind is unnecessary."
    }

    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $record = [ordered]@{
        schema_version = 2
        task_sha256 = $newDigest
        submitted_run_id = [string]$existing.submitted_run_id
        submitted_attempt = $submittedAttempt
        owner_run_id = $RunId
        owner_attempt = $RunAttempt
        created_at = $createdAt
        claimed_at = $now
        claim_count = $claimCount + 1
        source = "recovery"
        previous_task_sha256 = $oldDigest
        rebind_reason = "owner_authorized_task_content_reconciliation"
        rebind_authorization_run_id = $RunId
        rebound_at = $now
    }

    $nonce = [guid]::NewGuid().ToString('N')
    $backupPath = "$Path.quarantine-rebound-$RunId-$RunAttempt-$nonce.json"
    $temporaryPath = "$Path.$RunId-$RunAttempt-$nonce.rebind.tmp"
    try {
        [System.IO.File]::WriteAllText(
            $temporaryPath,
            ($record | ConvertTo-Json -Depth 5 -Compress),
            [System.Text.UTF8Encoding]::new($false)
        )
        [System.IO.File]::Replace($temporaryPath, $Path, $backupPath, $true)
    } catch {
        throw "Explicit owner reconciliation failed; the original owner record remains preserved. $($_.Exception.Message)"
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }

    return [pscustomobject]$record
}

Export-ModuleMember -Function Rebind-AutonomousTaskOwnerRecord
 -or
        [string]$VerifiedOwnerRun.path -cne ".github/workflows/autonomous-codex-engineering.yml" -or
        [string]$VerifiedOwnerRun.head_branch -cne "main" -or
        [string]$VerifiedOwnerRun.status -cne "completed" -or
        [string]$VerifiedOwnerRun.conclusion -notin @("failure", "cancelled", "timed_out")
    ) {
        throw "The previous owner run is not verified as a terminal unsuccessful main-branch execution."
    }

    try {
        $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
        $existing = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Pending task owner metadata cannot be read safely; preserving all state."
    }

    $ownerAttempt = 0
    $submittedAttempt = 0
    $claimCount = 0
    $createdAt = 0.0
    $claimedAt = 0.0
    $oldDigest = [string]$existing.task_sha256
    if (
        $existing.schema_version -is [bool] -or
        -not ($existing.schema_version -is [int] -or $existing.schema_version -is [long]) -or
        [long]$existing.schema_version -ne 1 -or
        $oldDigest -notmatch '^[0-9a-f]{64}$' -or
        [string]$existing.owner_run_id -cne [string]$VerifiedOwnerRun.id -or
        [string]$existing.owner_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.owner_attempt, [ref]$ownerAttempt) -or $ownerAttempt -lt 1 -or
        -not [int]::TryParse([string]$existing.submitted_attempt, [ref]$submittedAttempt) -or $submittedAttempt -lt 1 -or
        [string]$existing.submitted_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.claim_count, [ref]$claimCount) -or $claimCount -lt 1 -or
        -not [double]::TryParse([string]$existing.created_at, [ref]$createdAt) -or
        [double]::IsNaN($createdAt) -or [double]::IsInfinity($createdAt) -or $createdAt -le 0 -or
        -not [double]::TryParse([string]$existing.claimed_at, [ref]$claimedAt) -or
        [double]::IsNaN($claimedAt) -or [double]::IsInfinity($claimedAt) -or $claimedAt -le 0 -or
        [string]$existing.source -notin @("manual_dispatch", "recovery", "legacy_migration")
    ) {
        throw "Pending task owner metadata is not structurally valid schema v1; preserving it."
    }

    $newDigest = Get-AutonomousTaskDigest -Task $Task
    if ($oldDigest -ceq $newDigest) {
        throw "Pending task owner digest already matches; explicit rebind is unnecessary."
    }

    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $record = [ordered]@{
        schema_version = 2
        task_sha256 = $newDigest
        submitted_run_id = [string]$existing.submitted_run_id
        submitted_attempt = $submittedAttempt
        owner_run_id = $RunId
        owner_attempt = $RunAttempt
        created_at = $createdAt
        claimed_at = $now
        claim_count = $claimCount + 1
        source = "recovery"
        previous_task_sha256 = $oldDigest
        rebind_reason = "owner_authorized_task_content_reconciliation"
        rebind_authorization_run_id = $RunId
        rebound_at = $now
    }

    $nonce = [guid]::NewGuid().ToString('N')
    $backupPath = "$Path.quarantine-rebound-$RunId-$RunAttempt-$nonce.json"
    $temporaryPath = "$Path.$RunId-$RunAttempt-$nonce.rebind.tmp"
    try {
        [System.IO.File]::WriteAllText(
            $temporaryPath,
            ($record | ConvertTo-Json -Depth 5 -Compress),
            [System.Text.UTF8Encoding]::new($false)
        )
        [System.IO.File]::Replace($temporaryPath, $Path, $backupPath, $true)
    } catch {
        throw "Explicit owner reconciliation failed; the original owner record remains preserved. $($_.Exception.Message)"
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }

    return [pscustomobject]$record
}

Export-ModuleMember -Function Rebind-AutonomousTaskOwnerRecord
 -or $RunAttempt -lt 1) {
        throw "A numeric current workflow run ID and positive attempt are required."
    }
    if (
        [string]$VerifiedOwnerRun.id -notmatch '^[0-9]+$' -or
        [string]$VerifiedOwnerRun.path -cne ".github/workflows/autonomous-codex-engineering.yml" -or
        [string]$VerifiedOwnerRun.head_branch -cne "main" -or
        [string]$VerifiedOwnerRun.status -cne "completed" -or
        [string]$VerifiedOwnerRun.conclusion -notin @("failure", "cancelled", "timed_out")
    ) {
        throw "The previous owner run is not verified as a terminal unsuccessful main-branch execution."
    }

    try {
        $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
        $existing = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Pending task owner metadata cannot be read safely; preserving all state."
    }

    $ownerAttempt = 0
    $submittedAttempt = 0
    $claimCount = 0
    $createdAt = 0.0
    $claimedAt = 0.0
    $oldDigest = [string]$existing.task_sha256
    if (
        $existing.schema_version -is [bool] -or
        -not ($existing.schema_version -is [int] -or $existing.schema_version -is [long]) -or
        [long]$existing.schema_version -ne 1 -or
        $oldDigest -notmatch '^[0-9a-f]{64}$' -or
        [string]$existing.owner_run_id -cne [string]$VerifiedOwnerRun.id -or
        [string]$existing.owner_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.owner_attempt, [ref]$ownerAttempt) -or $ownerAttempt -lt 1 -or
        -not [int]::TryParse([string]$existing.submitted_attempt, [ref]$submittedAttempt) -or $submittedAttempt -lt 1 -or
        [string]$existing.submitted_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.claim_count, [ref]$claimCount) -or $claimCount -lt 1 -or
        -not [double]::TryParse([string]$existing.created_at, [ref]$createdAt) -or
        [double]::IsNaN($createdAt) -or [double]::IsInfinity($createdAt) -or $createdAt -le 0 -or
        -not [double]::TryParse([string]$existing.claimed_at, [ref]$claimedAt) -or
        [double]::IsNaN($claimedAt) -or [double]::IsInfinity($claimedAt) -or $claimedAt -le 0 -or
        [string]$existing.source -notin @("manual_dispatch", "recovery", "legacy_migration")
    ) {
        throw "Pending task owner metadata is not structurally valid schema v1; preserving it."
    }

    $newDigest = Get-AutonomousTaskDigest -Task $Task
    if ($oldDigest -ceq $newDigest) {
        throw "Pending task owner digest already matches; explicit rebind is unnecessary."
    }

    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $record = [ordered]@{
        schema_version = 2
        task_sha256 = $newDigest
        submitted_run_id = [string]$existing.submitted_run_id
        submitted_attempt = $submittedAttempt
        owner_run_id = $RunId
        owner_attempt = $RunAttempt
        created_at = $createdAt
        claimed_at = $now
        claim_count = $claimCount + 1
        source = "recovery"
        previous_task_sha256 = $oldDigest
        rebind_reason = "owner_authorized_task_content_reconciliation"
        rebind_authorization_run_id = $RunId
        rebound_at = $now
    }

    $nonce = [guid]::NewGuid().ToString('N')
    $backupPath = "$Path.quarantine-rebound-$RunId-$RunAttempt-$nonce.json"
    $temporaryPath = "$Path.$RunId-$RunAttempt-$nonce.rebind.tmp"
    try {
        [System.IO.File]::WriteAllText(
            $temporaryPath,
            ($record | ConvertTo-Json -Depth 5 -Compress),
            [System.Text.UTF8Encoding]::new($false)
        )
        [System.IO.File]::Replace($temporaryPath, $Path, $backupPath, $true)
    } catch {
        throw "Explicit owner reconciliation failed; the original owner record remains preserved. $($_.Exception.Message)"
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }

    return [pscustomobject]$record
}

Export-ModuleMember -Function Rebind-AutonomousTaskOwnerRecord
 -or
        -not [int]::TryParse([string]$existing.owner_attempt, [ref]$ownerAttempt) -or $ownerAttempt -lt 1 -or
        -not [int]::TryParse([string]$existing.submitted_attempt, [ref]$submittedAttempt) -or $submittedAttempt -lt 1 -or
        [string]$existing.submitted_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.claim_count, [ref]$claimCount) -or $claimCount -lt 1 -or
        -not [double]::TryParse([string]$existing.created_at, [ref]$createdAt) -or
        [double]::IsNaN($createdAt) -or [double]::IsInfinity($createdAt) -or $createdAt -le 0 -or
        -not [double]::TryParse([string]$existing.claimed_at, [ref]$claimedAt) -or
        [double]::IsNaN($claimedAt) -or [double]::IsInfinity($claimedAt) -or $claimedAt -le 0 -or
        [string]$existing.source -notin @("manual_dispatch", "recovery", "legacy_migration")
    ) {
        throw "Pending task owner metadata is not structurally valid schema v1; preserving it."
    }

    $newDigest = Get-AutonomousTaskDigest -Task $Task
    if ($oldDigest -ceq $newDigest) {
        throw "Pending task owner digest already matches; explicit rebind is unnecessary."
    }

    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $record = [ordered]@{
        schema_version = 2
        task_sha256 = $newDigest
        submitted_run_id = [string]$existing.submitted_run_id
        submitted_attempt = $submittedAttempt
        owner_run_id = $RunId
        owner_attempt = $RunAttempt
        created_at = $createdAt
        claimed_at = $now
        claim_count = $claimCount + 1
        source = "recovery"
        previous_task_sha256 = $oldDigest
        rebind_reason = "owner_authorized_task_content_reconciliation"
        rebind_authorization_run_id = $RunId
        rebound_at = $now
    }

    $nonce = [guid]::NewGuid().ToString('N')
    $backupPath = "$Path.quarantine-rebound-$RunId-$RunAttempt-$nonce.json"
    $temporaryPath = "$Path.$RunId-$RunAttempt-$nonce.rebind.tmp"
    try {
        [System.IO.File]::WriteAllText(
            $temporaryPath,
            ($record | ConvertTo-Json -Depth 5 -Compress),
            [System.Text.UTF8Encoding]::new($false)
        )
        [System.IO.File]::Replace($temporaryPath, $Path, $backupPath, $true)
    } catch {
        throw "Explicit owner reconciliation failed; the original owner record remains preserved. $($_.Exception.Message)"
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }

    return [pscustomobject]$record
}

Export-ModuleMember -Function Rebind-AutonomousTaskOwnerRecord
 -or $RunAttempt -lt 1) {
        throw "A numeric current workflow run ID and positive attempt are required."
    }
    if (
        [string]$VerifiedOwnerRun.id -notmatch '^[0-9]+$' -or
        [string]$VerifiedOwnerRun.path -cne ".github/workflows/autonomous-codex-engineering.yml" -or
        [string]$VerifiedOwnerRun.head_branch -cne "main" -or
        [string]$VerifiedOwnerRun.status -cne "completed" -or
        [string]$VerifiedOwnerRun.conclusion -notin @("failure", "cancelled", "timed_out")
    ) {
        throw "The previous owner run is not verified as a terminal unsuccessful main-branch execution."
    }

    try {
        $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
        $existing = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Pending task owner metadata cannot be read safely; preserving all state."
    }

    $ownerAttempt = 0
    $submittedAttempt = 0
    $claimCount = 0
    $createdAt = 0.0
    $claimedAt = 0.0
    $oldDigest = [string]$existing.task_sha256
    if (
        $existing.schema_version -is [bool] -or
        -not ($existing.schema_version -is [int] -or $existing.schema_version -is [long]) -or
        [long]$existing.schema_version -ne 1 -or
        $oldDigest -notmatch '^[0-9a-f]{64}$' -or
        [string]$existing.owner_run_id -cne [string]$VerifiedOwnerRun.id -or
        [string]$existing.owner_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.owner_attempt, [ref]$ownerAttempt) -or $ownerAttempt -lt 1 -or
        -not [int]::TryParse([string]$existing.submitted_attempt, [ref]$submittedAttempt) -or $submittedAttempt -lt 1 -or
        [string]$existing.submitted_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.claim_count, [ref]$claimCount) -or $claimCount -lt 1 -or
        -not [double]::TryParse([string]$existing.created_at, [ref]$createdAt) -or
        [double]::IsNaN($createdAt) -or [double]::IsInfinity($createdAt) -or $createdAt -le 0 -or
        -not [double]::TryParse([string]$existing.claimed_at, [ref]$claimedAt) -or
        [double]::IsNaN($claimedAt) -or [double]::IsInfinity($claimedAt) -or $claimedAt -le 0 -or
        [string]$existing.source -notin @("manual_dispatch", "recovery", "legacy_migration")
    ) {
        throw "Pending task owner metadata is not structurally valid schema v1; preserving it."
    }

    $newDigest = Get-AutonomousTaskDigest -Task $Task
    if ($oldDigest -ceq $newDigest) {
        throw "Pending task owner digest already matches; explicit rebind is unnecessary."
    }

    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $record = [ordered]@{
        schema_version = 2
        task_sha256 = $newDigest
        submitted_run_id = [string]$existing.submitted_run_id
        submitted_attempt = $submittedAttempt
        owner_run_id = $RunId
        owner_attempt = $RunAttempt
        created_at = $createdAt
        claimed_at = $now
        claim_count = $claimCount + 1
        source = "recovery"
        previous_task_sha256 = $oldDigest
        rebind_reason = "owner_authorized_task_content_reconciliation"
        rebind_authorization_run_id = $RunId
        rebound_at = $now
    }

    $nonce = [guid]::NewGuid().ToString('N')
    $backupPath = "$Path.quarantine-rebound-$RunId-$RunAttempt-$nonce.json"
    $temporaryPath = "$Path.$RunId-$RunAttempt-$nonce.rebind.tmp"
    try {
        [System.IO.File]::WriteAllText(
            $temporaryPath,
            ($record | ConvertTo-Json -Depth 5 -Compress),
            [System.Text.UTF8Encoding]::new($false)
        )
        [System.IO.File]::Replace($temporaryPath, $Path, $backupPath, $true)
    } catch {
        throw "Explicit owner reconciliation failed; the original owner record remains preserved. $($_.Exception.Message)"
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }

    return [pscustomobject]$record
}

Export-ModuleMember -Function Rebind-AutonomousTaskOwnerRecord
 -or
        [string]$VerifiedOwnerRun.path -cne ".github/workflows/autonomous-codex-engineering.yml" -or
        [string]$VerifiedOwnerRun.head_branch -cne "main" -or
        [string]$VerifiedOwnerRun.status -cne "completed" -or
        [string]$VerifiedOwnerRun.conclusion -notin @("failure", "cancelled", "timed_out")
    ) {
        throw "The previous owner run is not verified as a terminal unsuccessful main-branch execution."
    }

    try {
        $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
        $existing = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Pending task owner metadata cannot be read safely; preserving all state."
    }

    $ownerAttempt = 0
    $submittedAttempt = 0
    $claimCount = 0
    $createdAt = 0.0
    $claimedAt = 0.0
    $oldDigest = [string]$existing.task_sha256
    if (
        $existing.schema_version -is [bool] -or
        -not ($existing.schema_version -is [int] -or $existing.schema_version -is [long]) -or
        [long]$existing.schema_version -ne 1 -or
        $oldDigest -notmatch '^[0-9a-f]{64}$' -or
        [string]$existing.owner_run_id -cne [string]$VerifiedOwnerRun.id -or
        [string]$existing.owner_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.owner_attempt, [ref]$ownerAttempt) -or $ownerAttempt -lt 1 -or
        -not [int]::TryParse([string]$existing.submitted_attempt, [ref]$submittedAttempt) -or $submittedAttempt -lt 1 -or
        [string]$existing.submitted_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.claim_count, [ref]$claimCount) -or $claimCount -lt 1 -or
        -not [double]::TryParse([string]$existing.created_at, [ref]$createdAt) -or
        [double]::IsNaN($createdAt) -or [double]::IsInfinity($createdAt) -or $createdAt -le 0 -or
        -not [double]::TryParse([string]$existing.claimed_at, [ref]$claimedAt) -or
        [double]::IsNaN($claimedAt) -or [double]::IsInfinity($claimedAt) -or $claimedAt -le 0 -or
        [string]$existing.source -notin @("manual_dispatch", "recovery", "legacy_migration")
    ) {
        throw "Pending task owner metadata is not structurally valid schema v1; preserving it."
    }

    $newDigest = Get-AutonomousTaskDigest -Task $Task
    if ($oldDigest -ceq $newDigest) {
        throw "Pending task owner digest already matches; explicit rebind is unnecessary."
    }

    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $record = [ordered]@{
        schema_version = 2
        task_sha256 = $newDigest
        submitted_run_id = [string]$existing.submitted_run_id
        submitted_attempt = $submittedAttempt
        owner_run_id = $RunId
        owner_attempt = $RunAttempt
        created_at = $createdAt
        claimed_at = $now
        claim_count = $claimCount + 1
        source = "recovery"
        previous_task_sha256 = $oldDigest
        rebind_reason = "owner_authorized_task_content_reconciliation"
        rebind_authorization_run_id = $RunId
        rebound_at = $now
    }

    $nonce = [guid]::NewGuid().ToString('N')
    $backupPath = "$Path.quarantine-rebound-$RunId-$RunAttempt-$nonce.json"
    $temporaryPath = "$Path.$RunId-$RunAttempt-$nonce.rebind.tmp"
    try {
        [System.IO.File]::WriteAllText(
            $temporaryPath,
            ($record | ConvertTo-Json -Depth 5 -Compress),
            [System.Text.UTF8Encoding]::new($false)
        )
        [System.IO.File]::Replace($temporaryPath, $Path, $backupPath, $true)
    } catch {
        throw "Explicit owner reconciliation failed; the original owner record remains preserved. $($_.Exception.Message)"
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }

    return [pscustomobject]$record
}

Export-ModuleMember -Function Rebind-AutonomousTaskOwnerRecord
 -or $RunAttempt -lt 1) {
        throw "A numeric current workflow run ID and positive attempt are required."
    }
    if (
        [string]$VerifiedOwnerRun.id -notmatch '^[0-9]+$' -or
        [string]$VerifiedOwnerRun.path -cne ".github/workflows/autonomous-codex-engineering.yml" -or
        [string]$VerifiedOwnerRun.head_branch -cne "main" -or
        [string]$VerifiedOwnerRun.status -cne "completed" -or
        [string]$VerifiedOwnerRun.conclusion -notin @("failure", "cancelled", "timed_out")
    ) {
        throw "The previous owner run is not verified as a terminal unsuccessful main-branch execution."
    }

    try {
        $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
        $existing = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Pending task owner metadata cannot be read safely; preserving all state."
    }

    $ownerAttempt = 0
    $submittedAttempt = 0
    $claimCount = 0
    $createdAt = 0.0
    $claimedAt = 0.0
    $oldDigest = [string]$existing.task_sha256
    if (
        $existing.schema_version -is [bool] -or
        -not ($existing.schema_version -is [int] -or $existing.schema_version -is [long]) -or
        [long]$existing.schema_version -ne 1 -or
        $oldDigest -notmatch '^[0-9a-f]{64}$' -or
        [string]$existing.owner_run_id -cne [string]$VerifiedOwnerRun.id -or
        [string]$existing.owner_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.owner_attempt, [ref]$ownerAttempt) -or $ownerAttempt -lt 1 -or
        -not [int]::TryParse([string]$existing.submitted_attempt, [ref]$submittedAttempt) -or $submittedAttempt -lt 1 -or
        [string]$existing.submitted_run_id -notmatch '^[0-9]+$' -or
        -not [int]::TryParse([string]$existing.claim_count, [ref]$claimCount) -or $claimCount -lt 1 -or
        -not [double]::TryParse([string]$existing.created_at, [ref]$createdAt) -or
        [double]::IsNaN($createdAt) -or [double]::IsInfinity($createdAt) -or $createdAt -le 0 -or
        -not [double]::TryParse([string]$existing.claimed_at, [ref]$claimedAt) -or
        [double]::IsNaN($claimedAt) -or [double]::IsInfinity($claimedAt) -or $claimedAt -le 0 -or
        [string]$existing.source -notin @("manual_dispatch", "recovery", "legacy_migration")
    ) {
        throw "Pending task owner metadata is not structurally valid schema v1; preserving it."
    }

    $newDigest = Get-AutonomousTaskDigest -Task $Task
    if ($oldDigest -ceq $newDigest) {
        throw "Pending task owner digest already matches; explicit rebind is unnecessary."
    }

    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $record = [ordered]@{
        schema_version = 2
        task_sha256 = $newDigest
        submitted_run_id = [string]$existing.submitted_run_id
        submitted_attempt = $submittedAttempt
        owner_run_id = $RunId
        owner_attempt = $RunAttempt
        created_at = $createdAt
        claimed_at = $now
        claim_count = $claimCount + 1
        source = "recovery"
        previous_task_sha256 = $oldDigest
        rebind_reason = "owner_authorized_task_content_reconciliation"
        rebind_authorization_run_id = $RunId
        rebound_at = $now
    }

    $nonce = [guid]::NewGuid().ToString('N')
    $backupPath = "$Path.quarantine-rebound-$RunId-$RunAttempt-$nonce.json"
    $temporaryPath = "$Path.$RunId-$RunAttempt-$nonce.rebind.tmp"
    try {
        [System.IO.File]::WriteAllText(
            $temporaryPath,
            ($record | ConvertTo-Json -Depth 5 -Compress),
            [System.Text.UTF8Encoding]::new($false)
        )
        [System.IO.File]::Replace($temporaryPath, $Path, $backupPath, $true)
    } catch {
        throw "Explicit owner reconciliation failed; the original owner record remains preserved. $($_.Exception.Message)"
    } finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }

    return [pscustomobject]$record
}

Export-ModuleMember -Function Rebind-AutonomousTaskOwnerRecord
