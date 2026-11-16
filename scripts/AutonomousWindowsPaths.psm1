Set-StrictMode -Version Latest

function Resolve-AutonomousStateDirectory {
    [CmdletBinding()]
    param(
        [Parameter()][AllowNull()][string]$ConfiguredPath,
        [Parameter()][AllowNull()][string]$ProgramDataPath
    )

    if (-not [string]::IsNullOrWhiteSpace($ConfiguredPath)) {
        $candidate = [Environment]::ExpandEnvironmentVariables($ConfiguredPath.Trim())
        if (-not [System.IO.Path]::IsPathRooted($candidate)) {
            throw "AUTONOMOUS_ENGINEERING_PROGRESS_DIR must be an absolute shared path."
        }
        return [System.IO.Path]::GetFullPath($candidate)
    }

    if ([string]::IsNullOrWhiteSpace($ProgramDataPath) -or -not [System.IO.Path]::IsPathRooted($ProgramDataPath)) {
        throw "PROGRAMDATA must resolve to an absolute shared path; per-user fallback is not supported on Windows."
    }
    return [System.IO.Path]::GetFullPath(
        (Join-Path $ProgramDataPath "PersonalAIAssistant\autonomous-engineering")
    )
}

function Test-AutonomousStateStorageAccess {
    [CmdletBinding()]
    param(
        [Parameter()][AllowNull()][string]$ConfiguredPath,
        [Parameter()][AllowNull()][string]$ProcessPath,
        [Parameter()][AllowNull()][string]$ProgramDataPath
    )

    if (-not [string]::IsNullOrWhiteSpace($ProcessPath)) {
        if ([string]::IsNullOrWhiteSpace($ConfiguredPath)) {
            throw "AUTONOMOUS_ENGINEERING_PROGRESS_DIR is process-scoped; configure one machine-wide shared state directory."
        }
        $processFull = [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($ProcessPath.Trim()))
        $machineFull = [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($ConfiguredPath.Trim()))
        if (-not $processFull.Equals($machineFull, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Process and machine state-directory settings differ."
        }
    }

    $directory = Resolve-AutonomousStateDirectory -ConfiguredPath $ConfiguredPath -ProgramDataPath $ProgramDataPath
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
        throw "Shared state directory does not exist: $directory"
    }
    $probe = Join-Path $directory (".storage-access-check-" + [guid]::NewGuid().ToString("N") + ".tmp")
    $lockPath = Join-Path $directory "autonomous-durable-state.lock"
    $stream = $null
    try {
        [System.IO.File]::WriteAllText($probe, "ok")
        Remove-Item -LiteralPath $probe -Force -ErrorAction Stop
        $stream = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::ReadWrite)
        if ($stream.Length -eq 0) {
            $stream.WriteByte(0)
            $stream.Flush()
        }
        $stream.Lock(0, 1)
        $stream.Unlock(0, 1)
        return $directory
    } finally {
        if ($stream) { $stream.Dispose() }
        if (Test-Path -LiteralPath $probe) { Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue }
    }
}

Export-ModuleMember -Function Resolve-AutonomousStateDirectory, Test-AutonomousStateStorageAccess

