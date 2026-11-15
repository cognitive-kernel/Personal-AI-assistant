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

function Set-AutonomousDirectoryAcl {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$Path,
        [Parameter(Mandatory=$true)][ValidatePattern("^S-1-")][string]$RunnerSid,
        [Parameter()][ValidatePattern("^S-1-")][string]$ReadOnlySid,
        [Parameter()][ValidatePattern("^S-1-")][string]$AdditionalModifySid
    )

    $icacls = Get-Command icacls.exe -ErrorAction Stop
    $modifySids = @($RunnerSid)
    if ($AdditionalModifySid -and $AdditionalModifySid -notin $modifySids) {
        $modifySids += $AdditionalModifySid
    }
    $allowedSids = @($modifySids)
    if ($ReadOnlySid -and $ReadOnlySid -notin $allowedSids) {
        $allowedSids += $ReadOnlySid
    }

    & $icacls.Source $Path /inheritance:r | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not disable inherited ACL entries on '$Path'."
    }

    $existingAcl = Get-Acl -LiteralPath $Path
    $existingSids = @(
        $existingAcl.Access | ForEach-Object {
            $identityReference = $_.IdentityReference
            try {
                $identityReference.Translate([Security.Principal.SecurityIdentifier]).Value
            } catch {
                if ($identityReference.Value -match "^S-1-") { $identityReference.Value }
                else { throw "Could not resolve an existing ACL principal on '$Path'." }
            }
        } | Select-Object -Unique
    )
    foreach ($sid in $existingSids) {
        if ($sid -notin $allowedSids) {
            & $icacls.Source $Path /remove "*$sid" | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "Could not remove an unconfigured principal from '$Path' ACL."
            }
        }
    }

    $grants = @($modifySids | ForEach-Object { "*$($_):(OI)(CI)M" })
    if ($ReadOnlySid -and $ReadOnlySid -notin $modifySids) {
        $grants += "*$($ReadOnlySid):(OI)(CI)RX"
    }
    $arguments = @($Path, "/grant:r") + $grants
    & $icacls.Source @arguments | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not apply least-privilege ACLs to '$Path' for the configured account SIDs."
    }

    $verifiedAcl = Get-Acl -LiteralPath $Path
    $unexpectedSids = @(
        $verifiedAcl.Access | ForEach-Object {
            $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        } | Where-Object { $_ -notin $allowedSids } | Select-Object -Unique
    )
    if ($unexpectedSids.Count -gt 0) {
        throw "Unexpected principals remain on '$Path' ACL: $($unexpectedSids -join ', ')."
    }
}


function Set-AutonomousStateLockAcl {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$Path,
        [Parameter(Mandatory=$true)][ValidatePattern("^S-1-")][string]$AppSid
    )

    $lockPath = [System.IO.Path]::GetFullPath($Path)
    $parent = [System.IO.Path]::GetDirectoryName($lockPath)
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw "The shared autonomous state directory must exist before its lock ACL is provisioned."
    }

    # Provision only the coordination file. The app identity remains read-only
    # on task, owner, PR, checkpoint, and progress data in the parent directory.
    $stream = [System.IO.File]::Open(
        $lockPath,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::ReadWrite
    )
    $stream.Dispose()

    $icacls = Get-Command icacls.exe -ErrorAction Stop
    & $icacls.Source $lockPath /grant:r "*$($AppSid):M" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not grant the application identity read/write access to the durable-state lock file."
    }

    $acl = Get-Acl -LiteralPath $lockPath
    $requiredRights = [Security.AccessControl.FileSystemRights]::ReadData -bor
        [Security.AccessControl.FileSystemRights]::WriteData
    $rules = $acl.GetAccessRules(
        $true,
        $false,
        [Security.Principal.SecurityIdentifier]
    )
    $verified = @($rules | Where-Object {
        $_.IdentityReference.Value -eq $AppSid -and
        $_.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
        (($_.FileSystemRights -band $requiredRights) -eq $requiredRights)
    })
    if ($verified.Count -eq 0) {
        throw "The application identity does not have verified explicit read/write access to the durable-state lock file."
    }
}

Export-ModuleMember -Function ConvertTo-AutonomousWindowsAccountName, Get-AutonomousLocalAccountName, Get-AutonomousWindowsAccountSid, Set-AutonomousDirectoryAcl, Set-AutonomousStateLockAcl
