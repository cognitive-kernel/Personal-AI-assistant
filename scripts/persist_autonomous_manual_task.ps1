$ErrorActionPreference = "Stop"

$manualTask = [string]$env:MANUAL_TASK
if ($manualTask.Length -gt 2000 -or $manualTask.IndexOf([char]0) -ge 0) {
    throw "Manual engineering task must be at most 2000 characters and contain no NUL bytes."
}
if ([string]::IsNullOrWhiteSpace($env:PROGRAMDATA)) {
    throw "PROGRAMDATA is unavailable; the manual task cannot be persisted safely."
}

$configuredStateDir = [string]$env:AUTONOMOUS_ENGINEERING_PROGRESS_DIR
if ([string]::IsNullOrWhiteSpace($configuredStateDir)) {
    $stateDir = Join-Path $env:PROGRAMDATA "PersonalAIAssistant\autonomous-engineering"
} else {
    $expandedStateDir = [Environment]::ExpandEnvironmentVariables($configuredStateDir)
    $stateDir = [System.IO.Path]::GetFullPath($expandedStateDir)
}
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
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

    $workflowRunsUri = "https://api.github.com/repos/$env:GH_REPO/actions/workflows/autonomous-codex-engineering.yml/runs?per_page=100"
    $workflowRuns = Invoke-RestMethod -Method Get -Uri $workflowRunsUri -Headers @{
        Accept = "application/vnd.github+json"
        Authorization = "Bearer $env:GH_TOKEN"
        "X-GitHub-Api-Version" = "2022-11-28"
    } -TimeoutSec 25
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
    $pendingPrRaw = ""
    try {
        $pendingPrRaw = [System.IO.File]::ReadAllText($pendingPrPath, [System.Text.Encoding]::UTF8)
    } catch [System.IO.FileNotFoundException] {
        $pendingPrRaw = ""
    } catch [System.IO.DirectoryNotFoundException] {
        $pendingPrRaw = ""
    } catch {
        throw "Pending autonomous PR state cannot be read; refusing to replace its task. $($_.Exception.Message)"
    }
    if (-not [string]::IsNullOrWhiteSpace($pendingPrRaw)) {
        try {
            $pendingPrState = $pendingPrRaw | ConvertFrom-Json
            if (
                -not $pendingPrState.number -or
                [string]$pendingPrState.head_sha -notmatch '^[0-9a-f]{40}    if ($existingTask -and $existingTask -cne $manualTask) {
        throw "Another manual task is still pending. It cannot be replaced before its verified merge."
    }
    
    $headers = @{
        Accept = "application/vnd.github+json"
        Authorization = "Bearer $env:GH_TOKEN"
        "X-GitHub-Api-Version" = "2022-11-28"
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
        throw "A previous autonomous task PR is still open. Recover and accept it before submitting another manual task."
    }
    
    if (-not $existingTask) {
        $temporaryPath = Join-Path $stateDir ("pending-manual-task-" + $env:GITHUB_RUN_ID + ".tmp")
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
    Write-Host "Manual task persisted to the durable runner state before prerequisite checks."
} finally {
    if ($stateMutexOwned) {
        $stateMutex.ReleaseMutex()
    }
    $stateMutex.Dispose()
}
 -or
                [string]$pendingPrState.base_sha -notmatch '^[0-9a-f]{40}    if ($existingTask -and $existingTask -cne $manualTask) {
        throw "Another manual task is still pending. It cannot be replaced before its verified merge."
    }
    
    $headers = @{
        Accept = "application/vnd.github+json"
        Authorization = "Bearer $env:GH_TOKEN"
        "X-GitHub-Api-Version" = "2022-11-28"
    }
    $pullsUri = "https://api.github.com/repos/$env:GH_REPO/pulls?state=open&per_page=100"
    $pulls = Invoke-RestMethod -Method Get -Uri $pullsUri -Headers $headers -TimeoutSec 25
    $pendingPr = @($pulls | Where-Object {
        $_.state -eq "open" -and
        $_.user.login -eq "github-actions[bot]" -and
        $_.head.repo.full_name -eq $env:GH_REPO -and
        $_.head.ref -like "codex/local-engineering-*" -and
        $_.base.ref -eq "main"
    })
    if ($pendingPr.Count -gt 0) {
        throw "A previous autonomous task PR is still open. Recover and accept it before submitting another manual task."
    }
    
    if (-not $existingTask) {
        $temporaryPath = Join-Path $stateDir ("pending-manual-task-" + $env:GITHUB_RUN_ID + ".tmp")
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
    Write-Host "Manual task persisted to the durable runner state before prerequisite checks."
} finally {
    if ($stateMutexOwned) {
        $stateMutex.ReleaseMutex()
    }
    $stateMutex.Dispose()
}
 -or
                [string]$pendingPrState.branch -notlike "codex/local-engineering-*" -or
                [string]::IsNullOrWhiteSpace([string]$pendingPrState.run_id) -or
                [int]$pendingPrState.task -lt 1
            ) {
                throw "Pending PR metadata is incomplete or invalid."
            }
        } catch {
            throw "A pending autonomous PR state exists but is invalid; refusing to replace its task. $($_.Exception.Message)"
        }
        throw "Autonomous PR #$($pendingPrState.number) is awaiting owner merge or post-merge validation. Resume that PR before submitting a new task."
    }
    $existingTask = ""
    try {
        $existingTask = [System.IO.File]::ReadAllText($pendingPath, [System.Text.Encoding]::UTF8)
    } catch [System.IO.FileNotFoundException] {
        $existingTask = ""
    } catch [System.IO.DirectoryNotFoundException] {
        $existingTask = ""
    } catch {
        throw "Pending manual task state cannot be read safely. $($_.Exception.Message)"
    }
    if ($existingTask -and $existingTask -cne $manualTask) {
        throw "Another manual task is still pending. It cannot be replaced before its verified merge."
    }
    
    $headers = @{
        Accept = "application/vnd.github+json"
        Authorization = "Bearer $env:GH_TOKEN"
        "X-GitHub-Api-Version" = "2022-11-28"
    }
    $pullsUri = "https://api.github.com/repos/$env:GH_REPO/pulls?state=open&per_page=100"
    $pulls = Invoke-RestMethod -Method Get -Uri $pullsUri -Headers $headers -TimeoutSec 25
    $pendingPr = @($pulls | Where-Object {
        $_.state -eq "open" -and
        $_.user.login -eq "github-actions[bot]" -and
        $_.head.repo.full_name -eq $env:GH_REPO -and
        $_.head.ref -like "codex/local-engineering-*" -and
        $_.base.ref -eq "main"
    })
    if ($pendingPr.Count -gt 0) {
        throw "A previous autonomous task PR is still open. Recover and accept it before submitting another manual task."
    }
    
    if (-not $existingTask) {
        $temporaryPath = Join-Path $stateDir ("pending-manual-task-" + $env:GITHUB_RUN_ID + ".tmp")
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
    Write-Host "Manual task persisted to the durable runner state before prerequisite checks."
} finally {
    if ($stateMutexOwned) {
        $stateMutex.ReleaseMutex()
    }
    $stateMutex.Dispose()
}
