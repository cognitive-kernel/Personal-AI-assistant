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
        [Parameter()][ValidatePattern("^S-1-")][string]$AdditionalModifySid
    )

    $icacls = Get-Command icacls.exe -ErrorAction Stop
    $modifySids = @($RunnerSid)
    if ($AdditionalModifySid -and $AdditionalModifySid -notin $modifySids) {
        $modifySids += $AdditionalModifySid
    }
    $allowedSids = @($modifySids)

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


function Set-AutonomousDirectoryTreeAcl {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$Path,
        [Parameter(Mandatory=$true)][ValidatePattern("^S-1-")][string[]]$ModifySids,
        [Parameter(Mandatory=$true)][ValidatePattern("^S-1-")][string]$OwnerSid,
        [Parameter(Mandatory=$true)][ValidatePattern("^S-1-")][string]$InstallerSid
    )

    $root = [System.IO.Path]::GetFullPath($Path)
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        throw "The shared autonomous state root must be an existing directory."
    }
    $takeown = Get-Command takeown.exe -ErrorAction Stop
    & $takeown.Source /F $root /R /D Y | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not take temporary ownership of the protected state tree for ACL repair."
    }
    $icacls = Get-Command icacls.exe -ErrorAction Stop
    $installerGrant = "*$($InstallerSid):(OI)(CI)M"
    & $icacls.Source $root /grant:r $installerGrant /T /C /Q | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not grant temporary installer access to the protected state tree."
    }

    $rootItem = Get-Item -LiteralPath $root -Force -ErrorAction Stop
    if (-not $rootItem.PSIsContainer -or ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "The shared autonomous state root must be a real directory."
    }
    $items = @(Get-ChildItem -LiteralPath $root -Force -Recurse -ErrorAction Stop)
    foreach ($item in $items) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "The shared state tree contains a reparse point; refusing to change permissions."
        }
    }

    $allowed = @($ModifySids | Select-Object -Unique)
    if ($allowed.Count -lt 1) { throw "At least one state-owner SID is required." }
    $targets = @($items | Sort-Object -Property @{ Expression = { $_.FullName.Length }; Descending = $true }) + @($rootItem)
    foreach ($item in $targets) {
        $acl = Get-Acl -LiteralPath $item.FullName -ErrorAction Stop
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($rule in @($acl.Access)) {
            $acl.RemoveAccessRuleSpecific($rule)
        }
        foreach ($sid in $allowed) {
            $inheritance = if ($item.PSIsContainer) {
                [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
                    [Security.AccessControl.InheritanceFlags]::ObjectInherit
            } else {
                [Security.AccessControl.InheritanceFlags]::None
            }
            $newRule = [Security.AccessControl.FileSystemAccessRule]::new(
                [Security.Principal.SecurityIdentifier]$sid,
                [Security.AccessControl.FileSystemRights]::Modify,
                $inheritance,
                [Security.AccessControl.PropagationFlags]::None,
                [Security.AccessControl.AccessControlType]::Allow
            )
            $acl.SetAccessRule($newRule)
        }
        $acl.SetOwner([Security.Principal.SecurityIdentifier]$OwnerSid)
        Set-Acl -LiteralPath $item.FullName -AclObject $acl -ErrorAction Stop
        $verified = Get-Acl -LiteralPath $item.FullName -ErrorAction Stop
        $verifiedOwner = $verified.Owner
        $verifiedOwnerSid = ([Security.Principal.NTAccount]$verifiedOwner).Translate([Security.Principal.SecurityIdentifier]).Value
        if ($verifiedOwnerSid -ne $OwnerSid) {
            throw "Could not transfer ownership of '$($item.FullName)' to the broker service identity."
        }
        foreach ($rule in $verified.Access) {
            $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
            if ($sid -notin $allowed) {
                throw "Unexpected principal remains on '$($item.FullName)' ACL: $sid."
            }
        }
    }
}

Export-ModuleMember -Function ConvertTo-AutonomousWindowsAccountName, Get-AutonomousLocalAccountName, Get-AutonomousWindowsAccountSid, Set-AutonomousDirectoryAcl, Set-AutonomousDirectoryTreeAcl
