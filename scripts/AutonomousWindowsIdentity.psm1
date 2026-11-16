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
        [Parameter()][ValidatePattern("^S-1-")][string]$AdditionalModifySid,
        [Parameter()][ValidatePattern("^S-1-")][string[]]$AdditionalModifySids,
        [Parameter()][switch]$File
    )

    $icacls = Get-Command icacls.exe -ErrorAction Stop
    $modifySids = @($RunnerSid)
    $additionalSids = @()
    if ($AdditionalModifySid) { $additionalSids += $AdditionalModifySid }
    if ($AdditionalModifySids) { $additionalSids += $AdditionalModifySids }
    foreach ($sid in $additionalSids) {
        if ($sid -and $sid -notin $modifySids) {
            $modifySids += $sid
        }
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

    $grants = if ($File) {
        @($modifySids | ForEach-Object { "*$($_):M" })
    } else {
        @($modifySids | ForEach-Object { "*$($_):(OI)(CI)M" })
    }
    if ($ReadOnlySid -and $ReadOnlySid -notin $modifySids) {
        if ($File) {
            $grants += "*$($ReadOnlySid):R"
        } else {
            $grants += "*$($ReadOnlySid):(OI)(CI)RX"
        }
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


function Set-AutonomousStateDirectoryAcl {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$Path,
        [Parameter(Mandatory=$true)][ValidatePattern("^S-1-")][string]$RunnerSid,
        [Parameter()][ValidatePattern("^S-1-")][string[]]$AdditionalModifySids
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
    $lockPath = Join-Path $Path "autonomous-durable-state.lock"
    if (-not (Test-Path -LiteralPath $lockPath -PathType Leaf)) {
        [System.IO.File]::WriteAllBytes($lockPath, [byte[]]@(0))
    }
    Set-AutonomousDirectoryAcl -Path $Path -RunnerSid $RunnerSid -AdditionalModifySids $AdditionalModifySids
    Set-AutonomousDirectoryAcl -Path $lockPath -RunnerSid $RunnerSid -AdditionalModifySids $AdditionalModifySids -File
}

Export-ModuleMember -Function ConvertTo-AutonomousWindowsAccountName, Get-AutonomousLocalAccountName, Get-AutonomousWindowsAccountSid, Set-AutonomousDirectoryAcl, Set-AutonomousStateDirectoryAcl
