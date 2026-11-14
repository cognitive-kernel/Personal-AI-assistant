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

Export-ModuleMember -Function Resolve-AutonomousStateDirectory
