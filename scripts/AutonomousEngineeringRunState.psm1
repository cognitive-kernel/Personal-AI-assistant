Set-StrictMode -Version Latest

function Get-AutonomousWorkflowRun {
    param(
        [Parameter(Mandatory=$true)][string]$Repository,
        [Parameter(Mandatory=$true)][string]$RunId,
        [Parameter(Mandatory=$true)][int]$RunAttempt,
        [Parameter(Mandatory=$true)][hashtable]$Headers
    )
    if (
        $Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\z' -or
        $RunId -notmatch '^[0-9]{1,20}\z' -or
        $RunAttempt -lt 1
    ) {
        throw "A valid repository, numeric workflow run ID, and positive run attempt are required."
    }
    $uri = "https://api.github.com/repos/$Repository/actions/runs/$RunId/attempts/$RunAttempt"
    try {
        $run = Invoke-RestMethod -Method Get -Uri $uri -Headers $Headers -TimeoutSec 25
    } catch {
        throw "The exact recorded task-owner workflow run attempt could not be verified through GitHub."
    }
    if ([string]$run.id -cne $RunId -or [int]$run.run_attempt -ne $RunAttempt) {
        throw "GitHub returned a different workflow run or attempt than the recorded task owner."
    }
    return $run
}

function Assert-AutonomousWorkflowRunRebindable {
    param(
        [Parameter(Mandatory=$true)][object]$Run,
        [Parameter(Mandatory=$true)][string]$ExpectedRunId,
        [Parameter(Mandatory=$true)][int]$ExpectedRunAttempt
    )
    if (
        [string]$Run.id -cne $ExpectedRunId -or
        [int]$Run.run_attempt -ne $ExpectedRunAttempt -or
        [string]$Run.path -cne ".github/workflows/autonomous-codex-engineering.yml" -or
        [string]$Run.head_branch -cne "main" -or
        [string]$Run.status -cne "completed" -or
        [string]$Run.conclusion -cnotin @("failure", "cancelled", "timed_out")
    ) {
        throw "Task ownership cannot be rebound: GitHub has not verified the exact owner run attempt as a terminal unsuccessful main-branch execution."
    }
}

Export-ModuleMember -Function Get-AutonomousWorkflowRun, Assert-AutonomousWorkflowRunRebindable
