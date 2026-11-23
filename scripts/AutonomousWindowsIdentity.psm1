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
    # protected root itself for metadata can be denied to the elevated
    # installer before it has taken ownership.
    $rootParent = Split-Path -Parent $root
    $rootLeaf = Split-Path -Leaf $root
    $rootEntry = Get-ChildItem -LiteralPath $rootParent -Force -ErrorAction Stop |
        Where-Object { $_.Name -ceq $rootLeaf } |
        Select-Object -First 1
    if (-not $rootEntry -or -not $rootEntry.PSIsContainer -or
        ($rootEntry.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "The shared autonomous state root must be an existing, real directory."
    }
    $rootItem = [System.IO.DirectoryInfo]::new($root)

    $allowed = @($ModifySids | Select-Object -Unique)
    if ($allowed.Count -lt 1) { throw "At least one state-owner SID is required." }

    $takeown = Get-Command takeown.exe -ErrorAction Stop
    $icacls = Get-Command icacls.exe -ErrorAction Stop
    $items = New-Object 'System.Collections.Generic.List[System.IO.FileSystemInfo]'
    $touched = New-Object 'System.Collections.Generic.List[System.IO.FileSystemInfo]'
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
        # Validate the ACL object while the installer still owns the path.
        # After Set-Acl, the installer is intentionally removed from the DACL
        # and cannot reliably read the protected descriptor again.
        foreach ($rule in $acl.Access) {
            $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
            if ($sid -notin $allowed -or $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow) {
                throw "Unexpected principal or access rule was constructed for '$($Item.FullName)': $sid."
            }
        }
        Set-Acl -LiteralPath $Item.FullName -AclObject $acl -ErrorAction Stop
    }

    try {
        # The installer may not be able to enumerate a previously protected
        # state root. Take ownership and grant temporary access one directory
        # at a time; never use recursive takeown/icacls on an untrusted tree.
        # Every entry is checked for reparse points before it is opened or
        # descended into. The temporary installer ACE is removed by the final
        # least-privilege ACL pass (or by the failure cleanup below).
        while ($pendingDirectories.Count -gt 0) {
            $directoryEntry = $pendingDirectories.Pop()
            $directory = [string]$directoryEntry.Path
            $attributes = [IO.FileAttributes]$directoryEntry.Attributes
            if (($attributes -band [IO.FileAttributes]::Directory) -eq 0 -or
                ($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "The shared state tree contains a non-directory or reparse point; refusing to descend."
            }

            & $takeown.Source /F $directory | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "Could not take temporary ownership of directory '$directory' for ACL repair."
            }
            $directoryInfo = [System.IO.DirectoryInfo]::new($directory)
            $items.Add($directoryInfo)
            $touched.Add($directoryInfo)

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
            & $takeown.Source /F $item.FullName | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "Could not take temporary ownership of file '$($item.FullName)' for ACL repair."
            }
            $touched.Add($item)
            & $icacls.Source $item.FullName /grant:r ("*$($InstallerSid):M") /C /Q | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "Could not grant temporary installer access to file '$($item.FullName)'."
            }
        }

        $targets = @($items | Sort-Object -Property @{ Expression = { $_.FullName.Length }; Descending = $true })
        foreach ($item in $targets) {
            & $applyFinalAcl $item
            [void]$touched.Remove($item)
        }
        $completed = $true
    } finally {
        if (-not $completed) {
            # If preflight or an ACL operation fails, do not leave the
            # installer identity with residual access to any path already
            # opened. Restore the intended owner and least-privilege ACE set
            # for every path that was safely reached.
            $cleanupTargets = @($touched | Sort-Object -Property @{ Expression = { $_.FullName.Length }; Descending = $true })
            foreach ($item in $cleanupTargets) {
                try {
                    & $applyFinalAcl $item
                    [void]$touched.Remove($item)
                } catch {
                    Write-Warning "Could not finalize least-privilege ACL on '$($item.FullName)' after setup failure: $($_.Exception.Message)"
                }
            }
        }
    }
}

Export-ModuleMember -Function ConvertTo-AutonomousWindowsAccountName, Get-AutonomousLocalAccountName, Get-AutonomousWindowsAccountSid, Set-AutonomousDirectoryAcl, Set-AutonomousDirectoryTreeAcl
