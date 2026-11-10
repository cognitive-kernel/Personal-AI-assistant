$ErrorActionPreference = "Stop"

$manualTask = [string]$env:MANUAL_TASK
if ([string]::IsNullOrWhiteSpace($manualTask)) {
    throw "Manual engineering task must not be empty or whitespace."
}
if ($manualTask.Length -gt 2000 -or $manualTask.IndexOf([char]0) -ge 0) {
    throw "Manual engineering task must be at most 2000 characters and contain no NUL bytes."
}
if ([string]::IsNullOrWhiteSpace($env:GH_TOKEN) -or [string]::IsNullOrWhiteSpace($env:GH_REPO) -or [string]::IsNullOrWhiteSpace($env:GITHUB_RUN_ID)) {
    throw "GitHub repository, token, and run identity are required for safe task persistence."
}

$configuredStateDir = [string]$env:AUTONOMOUS_ENGINEERING_PROGRESS_DIR
if ([string]::IsNullOrWhiteSpace($configuredStateDir)) {
    if ([string]::IsNullOrWhiteSpace($env:PROGRAMDATA)) {
        throw "Neither AUTONOMOUS_ENGINEERING_PROGRESS_DIR nor PROGRAMDATA is available."
    }
    $stateDir = Join-Path $env:PROGRAMDATA "PersonalAIAssistant\autonomous-engineering"
} else {
    $expandedStateDir = [Environment]::ExpandEnvironmentVariables($configuredStateDir)
    $stateDir = [System.IO.Path]::GetFullPath($expandedStateDir)
}
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null

# This exact Local mutex name is also used by Python's durable_state_lock().
$mutexName = "Local\PersonalAIAssistant.AutonomousEngineering.State.v1"
$stateMutex = [System.Threading.Mutex]::new($false, $mutexName)
$stateMutexOwned = $false
try {
    try {
        $stateMutexOwned = $stateMutex.WaitOne([TimeSpan]::FromSeconds(60))
    } catch [System.Threading.AbandonedMutexException] {
        $stateMutexOwned = $true
        Write-Warning "Recovered an abandoned durable-state mutex; validating persisted state before proceeding."
    }
    if (-not $stateMutexOwned) {
        throw "Timed out waiting for the autonomous engineering durable-state mutex."
    }

    $headers = @{
        Accept = "application/vnd.github+json"
        Authorization = "Bearer $env:GH_TOKEN"
        "X-GitHub-Api-Version" = "2022-11-28"
    }
    $workflowRunsUri = "https://api.github.com/repos/$env:GH_REPO/actions/workflows/autonomous-codex-engineering.yml/runs?per_page=100"
    $workflowRuns = Invoke-RestMethod -Method Get -Uri $workflowRunsUri -Headers $headers -TimeoutSec 25
    if ($null -eq $workflowRuns -or $null -eq $workflowRuns.workflow_runs) {
        throw "GitHub returned an invalid workflow-run listing; refusing to accept a manual task."
    }
    $currentRunId = [string]$env:GITHUB_RUN_ID
    $otherActiveRuns = @($workflowRuns.workflow_runs | Where-Object {
        [string]$_.id -ne $currentRunId -and
        $_.status -in @("queued", "in_progress", "waiting", "requested", "pending")
    })
    if ($otherActiveRuns.Count -gt 0) {
        $activeIds = ($otherActiveRuns | ForEach-Object { [string]$_.id }) -join ", "
        throw "Another autonomous run is active or queued ($activeIds). The single-slot task store cannot safely accept a new task until that run finishes."
    }

    $pendingPath = Join-Path $stateDir "pending-manual-task.txt"
    $pendingPrPath = Join-Path $stateDir "pending-autonomous-pr.json"
    $taskOwnerPath = Join-Path $stateDir "pending-manual-task-owner.json"

    $pendingPrFound = $true
    try {
        $pendingPrRaw = [System.IO.File]::ReadAllText($pendingPrPath, [System.Text.Encoding]::UTF8)
    } catch [System.IO.FileNotFoundException] {
        $pendingPrFound = $false
        $pendingPrRaw = ""
    } catch [System.IO.DirectoryNotFoundException] {
        $pendingPrFound = $false
        $pendingPrRaw = ""
    } catch {
        throw "Pending autonomous PR state cannot be read; refusing to replace its task. $($_.Exception.Message)"
    }
    if ($pendingPrFound) {
        if ([string]::IsNullOrWhiteSpace($pendingPrRaw)) {
            throw "Pending autonomous PR state is empty; refusing to replace its task."
        }
        try {
            $pendingPrState = $pendingPrRaw | ConvertFrom-Json
            $pendingNumber = 0
            $pendingTaskNumber = 0
            if (
                -not [int]::TryParse([string]$pendingPrState.number, [ref]$pendingNumber) -or
                $pendingNumber -lt 1 -or
                [string]$pendingPrState.url -ne "https://github.com/$env:GH_REPO/pull/$pendingNumber" -or
                [string]$pendingPrState.head_sha -notmatch '^[0-9a-f]{40}$' -or
                [string]$pendingPrState.base_sha -notmatch '^[0-9a-f]{40}$' -or
                [string]$pendingPrState.branch -notlike "codex/local-engineering-*" -or
                [string]::IsNullOrWhiteSpace([string]$pendingPrState.run_id) -or
                -not [int]::TryParse([string]$pendingPrState.task, [ref]$pendingTaskNumber) -or
                $pendingTaskNumber -lt 1
            ) {
                throw "Pending PR metadata is incomplete or invalid."
            }
        } catch {
            throw "A pending autonomous PR state exists but is invalid; refusing to replace its task. $($_.Exception.Message)"
        }
        throw "Autonomous PR #$pendingNumber is awaiting owner merge or post-merge validation. Resume that PR before submitting a new task."
    }

    $existingTaskFound = $true
    try {
        $existingTask = [System.IO.File]::ReadAllText($pendingPath, [System.Text.Encoding]::UTF8)
    } catch [System.IO.FileNotFoundException] {
        $existingTaskFound = $false
        $existingTask = ""
    } catch [System.IO.DirectoryNotFoundException] {
        $existingTaskFound = $false
        $existingTask = ""
    } catch {
        throw "Pending manual task state cannot be read safely. $($_.Exception.Message)"
    }
    if ($existingTaskFound -and [string]::IsNullOrWhiteSpace($existingTask)) {
        throw "Pending manual task state is empty; refusing to overwrite unknown task ownership."
    }
    if ($existingTask -and $existingTask -cne $manualTask) {
        throw "Another manual task is still pending. It cannot be replaced before its verified merge."
    }
    $ownerFound = $true
    try {
        $ownerRaw = [System.IO.File]::ReadAllText($taskOwnerPath, [System.Text.Encoding]::UTF8)
    } catch [System.IO.FileNotFoundException] {
        $ownerFound = $false
        $ownerRaw = ""
    } catch [System.IO.DirectoryNotFoundException] {
        $ownerFound = $false
        $ownerRaw = ""
    } catch {
        throw "Pending task owner metadata cannot be read safely; preserving the task. $($_.Exception.Message)"
    }
    if (-not $existingTaskFound -and $ownerFound) {
        throw "Pending task owner metadata exists without its task; preserving unknown state."
    }

    $pullsUri = "https://api.github.com/repos/$env:GH_REPO/pulls?state=open&per_page=100"
    $pulls = @(Invoke-RestMethod -Method Get -Uri $pullsUri -Headers $headers -TimeoutSec 25)
    $pendingPr = @($pulls | Where-Object {
        $_.state -eq "open" -and
        $_.user.login -eq "github-actions[bot]" -and
        $_.head.repo.full_name -eq $env:GH_REPO -and
        $_.head.ref -like "codex/local-engineering-*" -and
        $_.base.ref -eq "main"
    })
    if ($pendingPr.Count -gt 0) {
        throw "A previous autonomous task PR is still open. Recover and accept it before submitting another task."
    }

    if (-not $existingTaskFound) {
        $temporaryPath = Join-Path $stateDir ("pending-manual-task-" + $env:GITHUB_RUN_ID + "-" + $env:GITHUB_RUN_ATTEMPT + ".tmp")
        try {
            [System.IO.File]::WriteAllText(
                $temporaryPath, $manualTask, [System.Text.UTF8Encoding]::new($false)
            )
            Move-Item -LiteralPath $temporaryPath -Destination $pendingPath -Force
        } finally {
            if (Test-Path -LiteralPath $temporaryPath) {
                Remove-Item -LiteralPath $temporaryPath -Force
            }
        }
    }

    $shaProvider = [System.Security.Cryptography.SHA256]::Create()
    try {
        $taskDigest = [BitConverter]::ToString(
            $shaProvider.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($manualTask))
        ).Replace("-", "").ToLowerInvariant()
    } finally {
        $shaProvider.Dispose()
    }
    $runAttempt = 0
    if (-not [int]::TryParse([string]$env:GITHUB_RUN_ATTEMPT, [ref]$runAttempt) -or $runAttempt -lt 1) {
        throw "Workflow run attempt is invalid; refusing to claim the pending task."
    }
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0

    if ($ownerFound) {
        try {
            if ([string]::IsNullOrWhiteSpace($ownerRaw)) { throw "Owner metadata is empty." }
            $ownerRecord = $ownerRaw | ConvertFrom-Json
            $ownerAttempt = 0
            $submittedAttempt = 0
            $claimCount = 0
            $createdAt = 0.0
            $claimedAt = 0.0
            if (
                $ownerRecord.schema_version -ne 1 -or
                [string]$ownerRecord.task_sha256 -cne $taskDigest -or
                [string]::IsNullOrWhiteSpace([string]$ownerRecord.owner_run_id) -or
                [string]::IsNullOrWhiteSpace([string]$ownerRecord.submitted_run_id) -or
                -not [int]::TryParse([string]$ownerRecord.owner_attempt, [ref]$ownerAttempt) -or
                $ownerAttempt -lt 1 -or
                -not [int]::TryParse([string]$ownerRecord.submitted_attempt, [ref]$submittedAttempt) -or
                $submittedAttempt -lt 1 -or
                -not [int]::TryParse([string]$ownerRecord.claim_count, [ref]$claimCount) -or
                $claimCount -lt 1 -or
                -not [double]::TryParse([string]$ownerRecord.created_at, [ref]$createdAt) -or
                [double]::IsNaN($createdAt) -or [double]::IsInfinity($createdAt) -or $createdAt -le 0 -or
                -not [double]::TryParse([string]$ownerRecord.claimed_at, [ref]$claimedAt) -or
                [double]::IsNaN($claimedAt) -or [double]::IsInfinity($claimedAt) -or $claimedAt -le 0 -or
                [string]$ownerRecord.source -notin @("manual_dispatch", "recovery", "legacy_migration")
            ) {
                throw "Owner metadata violates schema v1 or does not match the pending task."
            }
        } catch {
            throw "Pending task owner metadata is invalid; preserving the task. $($_.Exception.Message)"
        }
        if (
            [string]$ownerRecord.owner_run_id -ne [string]$env:GITHUB_RUN_ID -or
            $ownerAttempt -ne $runAttempt
        ) {
            $ownerRecord.owner_run_id = [string]$env:GITHUB_RUN_ID
            $ownerRecord.owner_attempt = $runAttempt
            $ownerRecord.claimed_at = $now
            $ownerRecord.claim_count = $claimCount + 1
        }
    } else {
        $source = if ($existingTaskFound) { "legacy_migration" } else { "manual_dispatch" }
        $ownerRecord = [ordered]@{
            schema_version = 1
            task_sha256 = $taskDigest
            submitted_run_id = [string]$env:GITHUB_RUN_ID
            submitted_attempt = $runAttempt
            owner_run_id = [string]$env:GITHUB_RUN_ID
            owner_attempt = $runAttempt
            created_at = $now
            claimed_at = $now
            claim_count = 1
            source = $source
        }
    }

    $ownerTemporaryPath = Join-Path $stateDir ("pending-manual-task-owner-" + $env:GITHUB_RUN_ID + "-" + $env:GITHUB_RUN_ATTEMPT + ".tmp")
    try {
        $ownerJson = $ownerRecord | ConvertTo-Json -Depth 5 -Compress
        [System.IO.File]::WriteAllText(
            $ownerTemporaryPath, $ownerJson, [System.Text.UTF8Encoding]::new($false)
        )
        Move-Item -LiteralPath $ownerTemporaryPath -Destination $taskOwnerPath -Force
    } finally {
        if (Test-Path -LiteralPath $ownerTemporaryPath) {
            Remove-Item -LiteralPath $ownerTemporaryPath -Force
        }
    }
    Write-Host "Manual task persisted to the durable runner state before prerequisite checks."
} finally {
    if ($stateMutexOwned) {
        $stateMutex.ReleaseMutex()
    }
    $stateMutex.Dispose()
}
