Set-StrictMode -Version Latest

# Get-Acl and Set-Acl are exported by this built-in module. Import it in this
# module scope so scriptblocks used during protected ACL reconciliation do
# not depend on implicit module auto-loading in an elevated PowerShell process.
Import-Module Microsoft.PowerShell.Security -ErrorAction Stop

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

    # Windows services commonly report local identities as ".\\user".
    # Normalize that form to "COMPUTER\\user" before SID translation so
    # service identities resolve consistently with provisioning parameters.
    $normalizedAccountName = ConvertTo-AutonomousWindowsAccountName -AccountName $AccountName
    try {
        return ([Security.Principal.NTAccount]$normalizedAccountName).Translate([Security.Principal.SecurityIdentifier]).Value
    } catch {
        throw "Could not resolve configured Windows principal '$AccountName' (normalized as '$normalizedAccountName') to a SID."
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
    # Inspect the directory entry through its accessible parent. Opening the
    # protected root itself for metadata can be denied to the elevated installer.
    $rootParent = Split-Path -Parent $root
    $rootLeaf = Split-Path -Leaf $root
    $rootEntry = Get-ChildItem -LiteralPath $rootParent -Force -ErrorAction Stop |
        Where-Object { $_.Name -ieq $rootLeaf } |
        Select-Object -First 1
    if (-not $rootEntry -or -not $rootEntry.PSIsContainer -or
        ($rootEntry.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "The shared autonomous state root must be an existing, real directory."
    }

    $allowed = @($ModifySids | Select-Object -Unique)
    if ($allowed.Count -lt 1) { throw "At least one state-owner SID is required." }

    $takeown = Get-Command takeown.exe -ErrorAction Stop
    $icacls = Get-Command icacls.exe -ErrorAction Stop
    $items = New-Object 'System.Collections.Generic.List[System.IO.FileSystemInfo]'
    $snapshots = New-Object 'System.Collections.Generic.List[object]'
    $pendingDirectories = New-Object 'System.Collections.Generic.Stack[object]'
    $pendingDirectories.Push([pscustomobject]@{ Path = $root; Attributes = $rootEntry.Attributes })
    $completed = $false

    $applyFinalAcl = {
        param([Parameter(Mandatory=$true)][System.IO.FileSystemInfo]$Item)

        $acl = Get-Acl -LiteralPath $Item.FullName -ErrorAction Stop
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($rule in @($acl.Access)) {
            $acl.RemoveAccessRuleSpecific($rule)
        }
        $inheritance = if ($Item -is [System.IO.DirectoryInfo]) {
            [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
                [Security.AccessControl.InheritanceFlags]::ObjectInherit
        } else {
            [Security.AccessControl.InheritanceFlags]::None
        }
        foreach ($sid in $allowed) {
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
        # Do not read the descriptor after this call: the installer is
        # intentionally excluded from the final protected DACL.
        Set-Acl -LiteralPath $Item.FullName -AclObject $acl -ErrorAction Stop
    }

    $restoreOriginalAcl = {
        param([Parameter(Mandatory=$true)]$Snapshot)
        Set-Acl -LiteralPath $Snapshot.Item.FullName -AclObject $Snapshot.Acl -ErrorAction Stop
    }

    try {
        # Preflight and repair directory access one level at a time. Temporary
        # access is journaled so a reparse point or other failure can restore
        # every descriptor reached so far. No recursive takeown/icacls is used.
        while ($pendingDirectories.Count -gt 0) {
            $directoryEntry = $pendingDirectories.Pop()
            $directory = [string]$directoryEntry.Path
            $attributes = [IO.FileAttributes]$directoryEntry.Attributes
            if (($attributes -band [IO.FileAttributes]::Directory) -eq 0 -or
                ($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "The shared state tree contains a non-directory or reparse point; refusing to descend."
            }

            $directoryInfo = [System.IO.DirectoryInfo]::new($directory)
            $originalOwnerSid = $OwnerSid
            $originalAcl = $null
            try {
                $originalAcl = Get-Acl -LiteralPath $directory -ErrorAction Stop
                $originalOwnerSid = ([Security.Principal.NTAccount]$originalAcl.Owner).Translate(
                    [Security.Principal.SecurityIdentifier]
                ).Value
            } catch {
                # A protected tree may deny the installer READ_CONTROL. Its
                # intended owner is the broker identity; capture the unchanged
                # DACL immediately after taking ownership below.
            }

            & $takeown.Source /F $directory | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "Could not take temporary ownership of directory '$directory' for ACL repair."
            }
            if ($null -eq $originalAcl) {
                $originalAcl = Get-Acl -LiteralPath $directory -ErrorAction Stop
                $originalAcl.SetOwner([Security.Principal.SecurityIdentifier]$originalOwnerSid)
            }
            $snapshots.Add([pscustomobject]@{ Item = $directoryInfo; Acl = $originalAcl })
            $items.Add($directoryInfo)

            $installerGrant = "*$($InstallerSid):(OI)(CI)M"
            & $icacls.Source $directory /grant:r $installerGrant /C /Q | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "Could not grant temporary installer access to directory '$directory'."
            }

            foreach ($entryInfo in Get-ChildItem -LiteralPath $directory -Force -ErrorAction Stop) {
                $entryAttributes = $entryInfo.Attributes
                if (($entryAttributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw "The shared state tree contains a reparse point; refusing to change permissions."
                }
                if ($entryInfo -is [System.IO.DirectoryInfo]) {
                    $pendingDirectories.Push([pscustomobject]@{
                        Path = $entryInfo.FullName
                        Attributes = $entryAttributes
                    })
                } else {
                    $items.Add($entryInfo)
                }
            }
        }

        # Files are touched only after the complete directory tree has passed
        # the reparse-point preflight.
        foreach ($item in @($items | Where-Object { $_ -is [System.IO.FileInfo] })) {
            $originalOwnerSid = $OwnerSid
            $originalAcl = $null
            try {
                $originalAcl = Get-Acl -LiteralPath $item.FullName -ErrorAction Stop
                $originalOwnerSid = ([Security.Principal.NTAccount]$originalAcl.Owner).Translate(
                    [Security.Principal.SecurityIdentifier]
                ).Value
            } catch {
                # Use the broker owner when the protected file descriptor is
                # not readable before temporary ownership is acquired.
            }

            & $takeown.Source /F $item.FullName | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "Could not take temporary ownership of file '$($item.FullName)' for ACL repair."
            }
            if ($null -eq $originalAcl) {
                $originalAcl = Get-Acl -LiteralPath $item.FullName -ErrorAction Stop
                $originalAcl.SetOwner([Security.Principal.SecurityIdentifier]$originalOwnerSid)
            }
            $snapshots.Add([pscustomobject]@{ Item = $item; Acl = $originalAcl })

            & $icacls.Source $item.FullName /grant:r ("*$($InstallerSid):M") /C /Q | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "Could not grant temporary installer access to file '$($item.FullName)'."
            }
        }

        $targets = @($items | Sort-Object -Property @{ Expression = { $_.FullName.Length }; Descending = $true })
        foreach ($item in $targets) {
            & $applyFinalAcl $item
        }
        $completed = $true
    } finally {
        if (-not $completed) {
            # Restore original descriptors and owners if preflight or final ACL
            # application fails. Parent paths are restored before children so
            # inherited ACLs are re-established from their original source.
            $restoreTargets = @($snapshots | Sort-Object -Property @{ Expression = { $_.Item.FullName.Length }; Descending = $false })
            foreach ($snapshot in $restoreTargets) {
                try {
                    & $restoreOriginalAcl $snapshot
                } catch {
                    Write-Warning "Could not restore original ACL on '$($snapshot.Item.FullName)' after setup failure: $($_.Exception.Message)"
                }
            }
        }
    }
}

Export-ModuleMember -Function ConvertTo-AutonomousWindowsAccountName, Get-AutonomousLocalAccountName, Get-AutonomousWindowsAccountSid, Set-AutonomousDirectoryAcl, Set-AutonomousDirectoryTreeAcl
