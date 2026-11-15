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
    if ($RunId -notmatch '^[0-9]{1,20}\z' -or $RunAttempt -lt 1) {
        throw "A numeric current workflow run ID and positive attempt are required."
    }
    if (
        [string]$VerifiedOwnerRun.id -notmatch '^[0-9]{1,20}\z' -or
        [string]$VerifiedOwnerRun.path -cne ".github/workflows/autonomous-codex-engineering.yml" -or
        [string]$VerifiedOwnerRun.head_branch -cne "main" -or
        [string]$VerifiedOwnerRun.status -cne "completed" -or
        [string]$VerifiedOwnerRun.conclusion -cnotin @("failure", "cancelled", "timed_out")
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
    if (
        -not [int]::TryParse([string]$existing.owner_attempt, [ref]$ownerAttempt) -or
        $ownerAttempt -lt 1
    ) {
        throw "The recorded owner attempt is invalid; preserving the owner record."
    }
    if ([int]$VerifiedOwnerRun.run_attempt -ne $ownerAttempt) {
        throw "GitHub verified a different attempt than the recorded task owner; preserving the owner record."
    }
    if ([string]$existing.owner_run_id -ceq $RunId -and $ownerAttempt -ge $RunAttempt) {
        throw "The current workflow run attempt already owns this task or is older than its recorded owner attempt."
    }
    $oldDigest = [string]$existing.task_sha256
    if (
        $existing.schema_version -is [bool] -or
        -not ($existing.schema_version -is [int] -or $existing.schema_version -is [long]) -or
        [long]$existing.schema_version -ne 1 -or
        -not ($existing.task_sha256 -is [string]) -or
        $oldDigest -cnotmatch '^[0-9a-f]{64}\z' -or
        -not ($existing.owner_run_id -is [string]) -or
        [string]$existing.owner_run_id -cne [string]$VerifiedOwnerRun.id -or
        [string]$existing.owner_run_id -notmatch '^[0-9]{1,20}\z' -or
        -not ($existing.owner_attempt -is [int] -or $existing.owner_attempt -is [long]) -or
        -not ($existing.submitted_attempt -is [int] -or $existing.submitted_attempt -is [long]) -or
        -not [int]::TryParse([string]$existing.submitted_attempt, [ref]$submittedAttempt) -or $submittedAttempt -lt 1 -or
        -not ($existing.submitted_run_id -is [string]) -or
        [string]$existing.submitted_run_id -notmatch '^[0-9]{1,20}\z' -or
        -not ($existing.claim_count -is [int] -or $existing.claim_count -is [long]) -or
        -not [int]::TryParse([string]$existing.claim_count, [ref]$claimCount) -or $claimCount -lt 1 -or
        -not ($existing.created_at -is [double] -or $existing.created_at -is [int] -or $existing.created_at -is [long]) -or
        -not [double]::TryParse([string]$existing.created_at, [ref]$createdAt) -or
        [double]::IsNaN($createdAt) -or [double]::IsInfinity($createdAt) -or $createdAt -le 0 -or
        -not ($existing.claimed_at -is [double] -or $existing.claimed_at -is [int] -or $existing.claimed_at -is [long]) -or
        -not [double]::TryParse([string]$existing.claimed_at, [ref]$claimedAt) -or
        [double]::IsNaN($claimedAt) -or [double]::IsInfinity($claimedAt) -or $claimedAt -le 0 -or
        -not ($existing.source -is [string]) -or
        [string]$existing.source -cnotin @("manual_dispatch", "recovery", "legacy_migration")
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
        rebind_authorization_run_attempt = $RunAttempt
        rebound_at = $now
    }

    $nonce = [guid]::NewGuid().ToString('N')
    $backupPath = "$Path.quarantine-rebound-$RunId-$RunAttempt-$nonce.json"
    $temporaryPath = "$Path.$RunId-$RunAttempt-$nonce.rebind.tmp"
    $temporaryCreated = $false
    try {
        $json = $record | ConvertTo-Json -Depth 5 -Compress
        $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($json)
        $stream = [System.IO.File]::Open(
            $temporaryPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $temporaryCreated = $true
        try {
            $stream.Write($bytes, 0, $bytes.Length)
            $stream.Flush($true)
        } finally {
            $stream.Dispose()
        }

        # Validate the exact bytes before replacing the live owner record.
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
