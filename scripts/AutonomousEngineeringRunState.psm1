Set-StrictMode -Version Latest

function Get-AutonomousWorkflowRun {
    param(
        [Parameter(Mandatory=$true)][string]$Repository,
        [Parameter(Mandatory=$true)][string]$RunId,
        [Parameter(Mandatory=$true)][hashtable]$Headers
    )
    if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' -or $RunId -notmatch '^[0-9]{1,20}$') {
        throw "A valid repository and numeric workflow run ID are required."
    }
    $uri = "https://api.github.com/repos/$Repository/actions/runs/$RunId"
    try {
        $run = Invoke-RestMethod -Method Get -Uri $uri -Headers $Headers -TimeoutSec 25
    } catch {
        throw "The recorded task-owner workflow run could not be verified through GitHub."
    }
    if ([string]$run.id -cne $RunId) {
        throw "GitHub returned a different workflow run than the recorded task owner."
    }
    return $run
}

function Assert-AutonomousWorkflowRunRebindable {
    param(
        [Parameter(Mandatory=$true)][object]$Run,
        [Parameter(Mandatory=$true)][string]$ExpectedRunId
    )
    if (
        [string]$Run.id -cne $ExpectedRunId -or
        [string]$Run.path -cne ".github/workflows/autonomous-codex-engineering.yml" -or
        [string]$Run.head_branch -cne "main" -or
        [string]$Run.status -cne "completed" -or
        [string]$Run.conclusion -notin @("failure", "cancelled", "timed_out")
    ) {
        throw "Task ownership cannot be rebound: GitHub has not verified the exact owner run as a terminal unsuccessful main-branch execution."
    }
}

Export-ModuleMember -Function Get-AutonomousWorkflowRun, Assert-AutonomousWorkflowRunRebindable
