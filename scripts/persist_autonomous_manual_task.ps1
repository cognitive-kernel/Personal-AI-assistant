$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot "AutonomousEngineeringTaskState.psm1") -Force

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
    $null = Repair-AutonomousPendingStateCleanup -StateDirectory $stateDir

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
    $pullsUri = "https://api.github.com/repos/$env:GH_REPO/pulls?state=open&per_page=100"
    $pulls = @(Invoke-RestMethod -Method Get -Uri $pullsUri -Headers $headers -TimeoutSec 25)
    $pendingPr = @($pulls | Where-Object {
        $_.state -eq "open" -and
        $_.user.login -eq "github-actions[bot]" -and
        $_.head.repo.full_name -eq $env:GH_REPO -and
        $_.head.ref -like "codex/local-engineering-*" -and
        $_.base.ref -eq "main"
    })

    function Write-RecoveredPendingPrRecord($PullRequest) {
        $branch = [string]$PullRequest.head.ref
        $branchMatch = [regex]::Match($branch, '^codex/local-engineering-(?<run>[0-9]+)-(?<attempt>[0-9]+)-task-(?<task>[0-9]+)$')
        $number = 0
        if (
            -not $branchMatch.Success -or
            -not [int]::TryParse([string]$PullRequest.number, [ref]$number) -or
            $number -lt 1 -or
            [string]$PullRequest.html_url -ne "https://github.com/$env:GH_REPO/pull/$number" -or
            [string]$PullRequest.head.sha -notmatch '^[0-9a-f]{40}$' -or
            [string]$PullRequest.base.sha -notmatch '^[0-9a-f]{40}$'
        ) {
            throw "GitHub autonomous PR metadata cannot be reconstructed safely; preserving local state."
        }
        $record = [ordered]@{
            number = $number
            url = [string]$PullRequest.html_url
            head_sha = [string]$PullRequest.head.sha
            base_sha = [string]$PullRequest.base.sha
            branch = $branch
            run_id = $branchMatch.Groups["run"].Value
            task = [int]$branchMatch.Groups["task"].Value
        }
        $temporaryPath = Join-Path $stateDir ("pending-autonomous-pr-" + $env:GITHUB_RUN_ID + "-" + $env:GITHUB_RUN_ATTEMPT + ".tmp")
        try {
            [System.IO.File]::WriteAllText(
                $temporaryPath,
                ($record | ConvertTo-Json -Depth 5 -Compress),
                [System.Text.UTF8Encoding]::new($false)
            )
            Move-Item -LiteralPath $temporaryPath -Destination $pendingPrPath -Force
        } finally {
            if (Test-Path -LiteralPath $temporaryPath) {
                Remove-Item -LiteralPath $temporaryPath -Force
            }
        }
        return $number
    }

    if ($pendingPr.Count -gt 1) {
        throw "Multiple open autonomous PRs exist; refusing to guess which task owns durable state."
    }
    if ($pendingPr.Count -eq 1) {
        $pendingNumber = Write-RecoveredPendingPrRecord -PullRequest $pendingPr[0]
        throw "Autonomous PR #$pendingNumber is awaiting owner merge or post-merge validation."
    }

    if ($pendingPrFound) {
        $localNumber = 0
        $livePendingPr = $null
        $localState = $null
        try {
            $localState = $pendingPrRaw | ConvertFrom-Json -ErrorAction Stop
        } catch {
            $localState = $null
        }
        if ($null -ne $localState -and [int]::TryParse([string]$localState.number, [ref]$localNumber) -and $localNumber -gt 0) {
            try {
                $livePendingPr = Invoke-RestMethod -Method Get -Uri "https://api.github.com/repos/$env:GH_REPO/pulls/$localNumber" -Headers $headers -TimeoutSec 25
            } catch {
                $statusCode = 0
                if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
                    $statusCode = [int]$_.Exception.Response.StatusCode
                }
                if ($statusCode -ne 404) {
                    throw "Could not verify stale pending PR metadata against GitHub; preserving it. $($_.Exception.Message)"
                }
            }
        }
        if (
            $null -ne $livePendingPr -and
            $livePendingPr.user.login -eq "github-actions[bot]" -and
            $livePendingPr.head.repo.full_name -eq $env:GH_REPO -and
            $livePendingPr.head.ref -like "codex/local-engineering-*" -and
            $livePendingPr.base.ref -eq "main"
        ) {
            $pendingNumber = Write-RecoveredPendingPrRecord -PullRequest $livePendingPr
            throw "Autonomous PR #$pendingNumber is awaiting owner merge or post-merge validation."
        }

        $quarantinePath = "$pendingPrPath.quarantine-$($env:GITHUB_RUN_ID)-$($env:GITHUB_RUN_ATTEMPT)-$([guid]::NewGuid().ToString('N')).json"
        try {
            Move-Item -LiteralPath $pendingPrPath -Destination $quarantinePath -ErrorAction Stop
            Write-Warning "Quarantined stale pending PR metadata after GitHub confirmed it is not an autonomous PR in this repository: $quarantinePath"
        } catch {
            throw "Stale pending PR metadata could not be quarantined safely. $($_.Exception.Message)"
        }
    }

    $runAttempt = 0
    if (-not [int]::TryParse([string]$env:GITHUB_RUN_ATTEMPT, [ref]$runAttempt) -or $runAttempt -lt 1) {
        throw "Workflow run attempt is invalid; refusing to claim the pending task."
    }
    if ($existingTaskFound) {
        $ownerRecord = Repair-AutonomousTaskOwnerRecord -Path $taskOwnerPath -Task $existingTask -RunId ([string]$env:GITHUB_RUN_ID) -RunAttempt $runAttempt -Source "legacy_migration"
    } else {
        $ownerRecord = Get-AutonomousTaskOwnerRecord -Path $taskOwnerPath -Task ""
        if ($null -ne $ownerRecord) {
            throw "Pending task owner metadata exists without its task; preserving unknown state."
        }
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

    $ownerSource = if ($existingTaskFound) { "legacy_migration" } else { "manual_dispatch" }
    $ownerArgs = @{
        Path = $taskOwnerPath
        Task = $manualTask
        RunId = [string]$env:GITHUB_RUN_ID
        RunAttempt = $runAttempt
        Source = $ownerSource
        Existing = $ownerRecord
    }
    $null = Write-AutonomousTaskOwnerRecord @ownerArgs

    Write-Host "Manual task persisted to the durable runner state before prerequisite checks."
} finally {
    if ($stateMutexOwned) {
        $stateMutex.ReleaseMutex()
    }
    $stateMutex.Dispose()
}
