Set-StrictMode -Version Latest

function Enter-AutonomousStateBrokerSetupMutex {
    [CmdletBinding()]
    param(
        [string]$Name = "Global\PersonalAIAssistant.StateBrokerSetup",
        [switch]$AcknowledgeAbandoned
    )

    if ([string]::IsNullOrWhiteSpace($Name)) {
        throw "A non-empty machine mutex name is required."
    }

    $mutex = $null
    $acquired = $false
    $abandoned = $false
    try {
        $mutex = [System.Threading.Mutex]::new($false, $Name)
        try {
            $acquired = $mutex.WaitOne(0)
        } catch [System.Threading.AbandonedMutexException] {
            # Windows grants ownership when WaitOne raises this exception.
            $acquired = $true
            $abandoned = $true
        }

        if (-not $acquired) {
            $mutex.Dispose()
            $mutex = $null
            throw "Another elevated state-broker setup is already running. No service or ACL changes were started."
        }

        return [pscustomobject]@{
            Mutex = $mutex
            Acquired = $true
            Abandoned = $abandoned
            AbandonedUnacknowledged = ($abandoned -and -not $AcknowledgeAbandoned)
        }
    } catch {
        if ($null -ne $mutex -and -not $acquired) {
            try { $mutex.Dispose() } catch {}
        }
        throw
    }
}

function Exit-AutonomousStateBrokerSetupMutex {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $false)]
        [AllowNull()]
        [object]$Lock
    )

    if ($null -eq $Lock -or $Lock.AbandonedUnacknowledged) {
        # Keep an unacknowledged abandoned owner until this process exits. The
        # next process will observe abandonment again and fail closed as well.
        return
    }

    if ($Lock.Acquired -and $null -ne $Lock.Mutex) {
        try {
            $Lock.Mutex.ReleaseMutex()
        } finally {
            $Lock.Mutex.Dispose()
        }
    }
}

Export-ModuleMember -Function Enter-AutonomousStateBrokerSetupMutex, Exit-AutonomousStateBrokerSetupMutex
