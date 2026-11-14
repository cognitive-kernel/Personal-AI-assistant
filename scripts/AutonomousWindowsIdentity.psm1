Set-StrictMode -Version Latest

function ConvertTo-AutonomousWindowsAccountName {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$AccountName)

    $value = $AccountName.Trim()
    if ($value.StartsWith(".\\", [StringComparison]::Ordinal)) {
        return "$env:COMPUTERNAME\\" + $value.Substring(2)
    }
    if ($value.Contains("\\") -or $value.Contains("@")) { return $value }
    return "$env:COMPUTERNAME\\" + $value
}

function Get-AutonomousLocalAccountName {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$AccountName)

    $prefix = "$env:COMPUTERNAME\\"
    if ($AccountName.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        return $AccountName.Substring($prefix.Length)
    }
    return $null
}

function Get-AutonomousWindowsAccountSid {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$AccountName)

    try {
        return ([Security.Principal.NTAccount]$AccountName).Translate([Security.Principal.SecurityIdentifier]).Value
    } catch {
        throw "Could not resolve configured Windows principal '$AccountName' to a SID."
    }
}

function Set-AutonomousDirectoryAcl {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$Path,
        [Parameter(Mandatory=$true)][ValidatePattern("^S-1-")][string]$RunnerSid,
        [Parameter()][ValidatePattern("^S-1-")][string]$ReadOnlySid
    )

    $icacls = Get-Command icacls.exe -ErrorAction Stop
    $grants = @("*$($RunnerSid):(OI)(CI)M")
    if ($ReadOnlySid -and $ReadOnlySid -ne $RunnerSid) {
        $grants += "*$($ReadOnlySid):(OI)(CI)RX"
    }
    $arguments = @($Path, "/inheritance:r", "/grant:r") + $grants
    & $icacls.Source @arguments | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not apply least-privilege ACLs to '$Path' for the configured account SIDs."
    }
}

Export-ModuleMember -Function ConvertTo-AutonomousWindowsAccountName, Get-AutonomousLocalAccountName, Get-AutonomousWindowsAccountSid, Set-AutonomousDirectoryAcl
