Set-StrictMode -Version Latest

$script:DefaultSetupMarkerRegistrySubKey = "SOFTWARE\PersonalAIAssistant\StateBroker"

function Test-AutonomousStateBrokerSetupRecoveryMarker {
    [CmdletBinding()]
    param([string]$RegistrySubKey = $script:DefaultSetupMarkerRegistrySubKey)
    if ([string]::IsNullOrWhiteSpace($RegistrySubKey)) {
        throw "A non-empty State Broker setup recovery-marker registry path is required."
    }
    $key = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey($RegistrySubKey, $false)
    if ($null -eq $key) { return $false }
    try {
        $value = $key.GetValue("SetupAbandoned", $null)
        return -not [string]::IsNullOrWhiteSpace([string]$value)
    } finally { $key.Dispose() }
}

function Set-AutonomousStateBrokerSetupRecoveryMarker {
    [CmdletBinding()]
    param([string]$RegistrySubKey = $script:DefaultSetupMarkerRegistrySubKey)
    if ([string]::IsNullOrWhiteSpace($RegistrySubKey)) {
        throw "A non-empty State Broker setup recovery-marker registry path is required."
    }
    $key = [Microsoft.Win32.Registry]::LocalMachine.CreateSubKey($RegistrySubKey)
    if ($null -eq $key) { throw "Could not create the protected State Broker setup recovery marker." }
    try {
        $key.SetValue("SetupAbandoned", [DateTimeOffset]::UtcNow.ToString("o"), [Microsoft.Win32.RegistryValueKind]::String)
    } finally { $key.Dispose() }
}

function Clear-AutonomousStateBrokerSetupRecoveryMarker {
    [CmdletBinding()]
    param([string]$RegistrySubKey = $script:DefaultSetupMarkerRegistrySubKey)
    if ([string]::IsNullOrWhiteSpace($RegistrySubKey)) {
        throw "A non-empty State Broker setup recovery-marker registry path is required."
    }
    $key = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey($RegistrySubKey, $true)
    if ($null -eq $key) { return }
    try { $key.DeleteValue("SetupAbandoned", $false) } finally { $key.Dispose() }
}

function Enter-AutonomousStateBrokerSetupMutex {
    [CmdletBinding()]
    param(
        [string]$Name = "Global\PersonalAIAssistant.StateBrokerSetup",
        [string]$MarkerRegistrySubKey = $script:DefaultSetupMarkerRegistrySubKey,
        [switch]$AcknowledgeAbandoned
    )
    if ([string]::IsNullOrWhiteSpace($Name)) {
        throw "A non-empty machine mutex name is required."
    }

    $markerPresent = Test-AutonomousStateBrokerSetupRecoveryMarker -RegistrySubKey $MarkerRegistrySubKey
    if ($markerPresent -and -not $AcknowledgeAbandoned) {
        throw "A previous elevated state-broker setup left an unacknowledged recovery marker. Inspect broker health, service state, and ACL integrity, then rerun with -AcknowledgeAbandonedSetup."
    }

    $mutex = $null
    $acquired = $false
    $abandoned = $false
    try {
        $mutex = [System.Threading.Mutex]::new($false, $Name)
        try {
            $acquired = $mutex.WaitOne(0)
        } catch [System.Threading.AbandonedMutexException] {
            $acquired = $true
            $abandoned = $true
        }

        if (-not $acquired) {
            $mutex.Dispose()
            $mutex = $null
            throw "Another elevated state-broker setup is already running. No service or ACL changes were started."
        }

        if ($abandoned -and -not $AcknowledgeAbandoned) {
            # Persist the recovery requirement before releasing the abandoned
            # mutex. This prevents same-process recursive acquisition from
            # bypassing the explicit acknowledgement requirement.
            Set-AutonomousStateBrokerSetupRecoveryMarker -RegistrySubKey $MarkerRegistrySubKey
            try { $mutex.ReleaseMutex() } finally {
                $mutex.Dispose()
                $mutex = $null
                $acquired = $false
            }
            throw "A previous elevated state-broker setup terminated while holding the machine-wide lock. A protected recovery marker was recorded. Inspect broker health, service state, and ACL integrity, then rerun with -AcknowledgeAbandonedSetup."
        }

        return [pscustomobject]@{
            Mutex = $mutex
            Acquired = $true
            Abandoned = $abandoned
            AbandonedUnacknowledged = $false
            RecoveryMarkerPresent = $markerPresent
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
    if ($null -eq $Lock) { return }
    if ($Lock.Acquired -and $null -ne $Lock.Mutex) {
        try { $Lock.Mutex.ReleaseMutex() } finally { $Lock.Mutex.Dispose() }
    }
}

Export-ModuleMember -Function Test-AutonomousStateBrokerSetupRecoveryMarker, Set-AutonomousStateBrokerSetupRecoveryMarker, Clear-AutonomousStateBrokerSetupRecoveryMarker, Enter-AutonomousStateBrokerSetupMutex, Exit-AutonomousStateBrokerSetupMutex
