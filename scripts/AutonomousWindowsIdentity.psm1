Set-StrictMode -Version Latest

function ConvertTo-AutonomousWindowsAccountName {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$AccountName)

    $value = $AccountName.Trim()
    if ($value.StartsWith(".\", [StringComparison]::Ordinal)) {
        return "$env:COMPUTERNAME\" + $value.Substring(2)
    }
    if ($value.Contains("\") -or $value.Contains("@")) { return $value }
    return "$env:COMPUTERNAME\" + $value
}

function Get-AutonomousLocalAccountName {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$AccountName)

    $prefix = "$env:COMPUTERNAME\"
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

function Get-AutonomousRunnerService {
    [CmdletBinding()]
    param(
        [Parameter()][AllowNull()][string]$RunnerRoot,
        [Parameter()][AllowNull()][string]$ServiceName
    )

    $services = @(Get-CimInstance Win32_Service -ErrorAction Stop | Where-Object {
        $isRunnerService = $_.PathName -match '(?i)(^|[\\/])RunnerService\.exe(?:\s|$)'
        $matchesRoot = [string]::IsNullOrWhiteSpace($RunnerRoot) -or
            $_.PathName.IndexOf([System.IO.Path]::GetFullPath($RunnerRoot), [StringComparison]::OrdinalIgnoreCase) -ge 0
        $matchesName = [string]::IsNullOrWhiteSpace($ServiceName) -or $_.Name -eq $ServiceName
        $isRunnerService -and $matchesRoot -and $matchesName
    })
    if ($services.Count -ne 1) {
        throw "Expected exactly one GitHub Runner Windows service for the configured Runner root; found $($services.Count). Set the Runner root or service selection explicitly when multiple services are installed."
    }
    return $services[0]
}

function Set-AutonomousDirectoryAcl {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$Path,
        [Parameter(Mandatory=$true)][ValidatePattern("^S-1-")][string]$RunnerSid,
        [Parameter()][ValidatePattern("^S-1-")][string]$ReadOnlySid,
        [Parameter()][ValidatePattern("^S-1-")][string]$AdditionalModifySid,
        [Parameter()][ValidatePattern("^S-1-")][string[]]$AdditionalModifySids,
        [Parameter()][switch]$File
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf -ErrorAction SilentlyContinue) -and
        -not (Test-Path -LiteralPath $Path -PathType Container -ErrorAction SilentlyContinue)) {
        throw "Cannot configure ACLs for a path that does not exist: '$Path'."
    }

    $modifySids = @($RunnerSid)
    $additionalSids = @()
    if ($AdditionalModifySid) { $additionalSids += $AdditionalModifySid }
    if ($AdditionalModifySids) { $additionalSids += $AdditionalModifySids }
    foreach ($sid in $additionalSids) {
        if ($sid -and $sid -notin $modifySids) { $modifySids += $sid }
    }

    $acl = Get-Acl -LiteralPath $Path -ErrorAction Stop
    $inheritance = if ($File) {
        [System.Security.AccessControl.InheritanceFlags]::None
    } else {
        [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
            [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    }

    foreach ($sid in $modifySids) {
        $principal = [System.Security.Principal.SecurityIdentifier]::new($sid)
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $principal,
            [System.Security.AccessControl.FileSystemRights]::Modify,
            $inheritance,
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        # AddAccessRule is additive: existing inherited, explicit, allow, and deny
        # entries remain intact. In particular, do not reset inheritance or replace
        # the DACL with a hand-built allow-list.
        $null = $acl.AddAccessRule($rule)
    }

    if ($ReadOnlySid -and $ReadOnlySid -notin $modifySids) {
        $principal = [System.Security.Principal.SecurityIdentifier]::new($ReadOnlySid)
        $rights = if ($File) {
            [System.Security.AccessControl.FileSystemRights]::Read
        } else {
            [System.Security.AccessControl.FileSystemRights]::ReadAndExecute
        }
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $principal,
            $rights,
            $inheritance,
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        $null = $acl.AddAccessRule($rule)
    }

    Set-Acl -LiteralPath $Path -AclObject $acl -ErrorAction Stop

    $verifiedAcl = Get-Acl -LiteralPath $Path -ErrorAction Stop
    $verifiedRules = $verifiedAcl.GetAccessRules(
        $true,
        $true,
        [System.Security.Principal.SecurityIdentifier]
    )
    foreach ($sid in $modifySids) {
        $hasModify = @($verifiedRules | Where-Object {
            $_.IdentityReference.Value -eq $sid -and
            $_.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
            (($_.FileSystemRights -band [System.Security.AccessControl.FileSystemRights]::Modify) -eq
                [System.Security.AccessControl.FileSystemRights]::Modify)
        }).Count -gt 0
        if (-not $hasModify) {
            throw "Modify access for SID '$sid' was not verified on '$Path'. Existing deny rules may still prevent effective access."
        }
    }
}

function Set-AutonomousStateDirectoryAcl {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$Path,
        [Parameter(Mandatory=$true)][ValidatePattern("^S-1-")][string]$RunnerSid,
        [Parameter()][ValidatePattern("^S-1-")][string[]]$AdditionalModifySids
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        New-Item -ItemType Directory -Path $Path -Force -ErrorAction Stop | Out-Null
    }
    $lockPath = Join-Path $Path "autonomous-durable-state.lock"
    $stream = [System.IO.File]::Open(
        $lockPath,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::ReadWrite
    )
    try {
        if ($stream.Length -eq 0) {
            $stream.WriteByte(0)
            $stream.Flush()
        }
    } finally {
        $stream.Dispose()
    }

    Set-AutonomousDirectoryAcl -Path $Path -RunnerSid $RunnerSid -AdditionalModifySids $AdditionalModifySids
    Set-AutonomousDirectoryAcl -Path $lockPath -RunnerSid $RunnerSid -AdditionalModifySids $AdditionalModifySids -File
}

Export-ModuleMember -Function ConvertTo-AutonomousWindowsAccountName, Get-AutonomousLocalAccountName, Get-AutonomousWindowsAccountSid, Get-AutonomousRunnerService, Set-AutonomousDirectoryAcl, Set-AutonomousStateDirectoryAcl
