[CmdletBinding()]
param(
    [switch]$Dev,
    [switch]$CheckOnly,
    [switch]$RuntimeOnly,
    [string]$RunnerToolCache,
    [string]$RunnerPythonArchive,
    [switch]$CheckRunnerRuntime
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = [System.IO.Path]::GetFullPath($PSScriptRoot)
$RuntimeRoot = Join-Path $Root ".runtime"
$UvVersion = "0.13.9"

$UvAssets = @{
    "x86_64-pc-windows-msvc" = @{
        Sha256 = "edd61f7f5728a56cc62455386661860bdfc7bab65872f39b0c25bdcc1d8e1beb"
    }
    "aarch64-pc-windows-msvc" = @{
        Sha256 = "72eed595983a4d7d22d79f21065b0f4479cc7e5c7a89104bb410c8f47559a878"
    }
    "i686-pc-windows-msvc" = @{
        Sha256 = "eb4bd7285a512ebc4122f65a21b72e3f7072610d62164f43ca6aca280c39f6fd"
    }
}

function Get-UvTarget {
    $architecture = [string]$env:PROCESSOR_ARCHITECTURE
    if (-not [string]::IsNullOrWhiteSpace($env:PROCESSOR_ARCHITEW6432)) {
        $architecture = [string]$env:PROCESSOR_ARCHITEW6432
    }
    switch ($architecture.ToUpperInvariant()) {
        "AMD64" { return "x86_64-pc-windows-msvc" }
        "ARM64" { return "aarch64-pc-windows-msvc" }
        "X86"   { return "i686-pc-windows-msvc" }
        default { throw "Unsupported Windows processor architecture '$architecture'. No matching verified uv runtime is configured." }
    }
}

function Test-UsablePythonEnvironment([string]$EnvironmentPath) {
    $python = Join-Path $EnvironmentPath "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { return $false }
    & $python -c "import sys, pip; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" *> $null
    return ($LASTEXITCODE -eq 0)
}

function Save-UvArchive([string]$Url, [string]$Archive) {
    $curl = Join-Path $env:SystemRoot "System32\curl.exe"
    if (Test-Path -LiteralPath $curl -PathType Leaf) {
        & $curl --disable --fail --location --silent --show-error --proto "=https" --proto-redir "=https" `
            --retry 2 --connect-timeout 15 --max-time 90 --output $Archive $Url
        if ($LASTEXITCODE -eq 0) { return }
        Write-Warning "Windows curl failed to download the verified uv release (exit $LASTEXITCODE); trying the PowerShell HTTP client."
        Remove-Item -LiteralPath $Archive -Force -ErrorAction SilentlyContinue
    }

    $previousProtocol = [System.Net.ServicePointManager]::SecurityProtocol
    try {
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $Url -OutFile $Archive -UseBasicParsing -TimeoutSec 90
    } catch {
        throw "Could not download uv $UvVersion from $Url under this Windows account. Check HTTPS access to github.com and release-assets.githubusercontent.com. $($_.Exception.Message)"
    } finally {
        [System.Net.ServicePointManager]::SecurityProtocol = $previousProtocol
    }
}

function Install-VerifiedUv([string]$Target) {
    $asset = $UvAssets[$Target]
    $downloadUrl = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-$Target.zip"
    $downloadRoot = Join-Path $ManagedRuntimeRoot ("download-" + [guid]::NewGuid().ToString("N"))
    $archive = Join-Path $downloadRoot "uv.zip"
    $extractRoot = Join-Path $downloadRoot "extract"
    $stagedUv = Join-Path $downloadRoot "uv.exe"

    New-Item -ItemType Directory -Path $downloadRoot, $extractRoot, $ToolsRoot -Force | Out-Null
    try {
        Save-UvArchive -Url $downloadUrl -Archive $archive
        $actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -ne $asset.Sha256) {
            throw "uv archive SHA-256 mismatch for $Target; refusing to execute the downloaded file."
        }

        Expand-Archive -LiteralPath $archive -DestinationPath $extractRoot -Force
        $binary = Get-ChildItem -LiteralPath $extractRoot -Filter "uv.exe" -File -Recurse |
            Select-Object -First 1
        if (-not $binary) { throw "The verified uv archive did not contain uv.exe." }
        Copy-Item -LiteralPath $binary.FullName -Destination $stagedUv -Force
        Move-Item -LiteralPath $stagedUv -Destination $UvPath -Force
    } finally {
        Remove-Item -LiteralPath $downloadRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Initialize-VerifiedUv {
    $target = Get-UvTarget
    if (Test-Path -LiteralPath $UvPath -PathType Leaf) {
        $version = (& $UvPath --version 2>$null | Out-String).Trim()
        if ($version -match ('^uv\s+' + [regex]::Escape($UvVersion) + '(\s|$)')) { return }
    }
    Write-Host "Installing verified uv $UvVersion for $target ..."
    Install-VerifiedUv -Target $target
}

function Find-LocalManagedPython {
    $ErrorActionPreference = "Continue"
    $PSNativeCommandUseErrorActionPreference = $false
    $python = & $UvPath python find --system --offline 3.11 2>$null
    if ($LASTEXITCODE -eq 0 -and $python -and (Test-Path -LiteralPath $python -PathType Leaf)) {
        return $python
    }
}

function Test-RunnerPython([string]$Executable) {
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $false }
    try {
        & $Executable -I -c "import ensurepip, sys, venv; raise SystemExit(sys.version.split()[0] != sys.argv[1])" $RunnerPackage.version *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Install-RunnerPython {
    if (-not (Test-Path -LiteralPath $RunnerPythonArchive -PathType Leaf)) {
        throw "The pinned Python archive is missing and this Runner has no usable cached copy. Check the Windows Python package job and artifact download."
    }
    if ((Get-FileHash -LiteralPath $RunnerPythonArchive -Algorithm SHA256).Hash -ne $RunnerPackage.sha256) {
        throw "Runner Python archive SHA-256 mismatch. The archive will not be extracted."
    }

    $staging = Join-Path $ManagedRuntimeRoot ("python-stage-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $staging -Force | Out-Null
    try {
        $tar = Join-Path $env:SystemRoot "System32\tar.exe"
        & $tar -xf $RunnerPythonArchive -C $staging
        if ($LASTEXITCODE -ne 0) { throw "Could not extract the verified Runner Python archive." }
        $stagedPython = Join-Path $staging "python"
        if (-not (Test-RunnerPython (Join-Path $stagedPython "python.exe"))) {
            throw "The verified Runner Python archive did not produce usable Python with venv and ensurepip."
        }
        if (Test-Path -LiteralPath $RunnerPythonRoot) {
            Remove-Item -LiteralPath $RunnerPythonRoot -Recurse -Force
        }
        Move-Item -LiteralPath $stagedPython -Destination $RunnerPythonRoot
        Write-Host "Installed verified Python $($RunnerPackage.version) from the Workflow artifact."
    } finally {
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
}

try {
    $ManagedRuntimeRoot = $RuntimeRoot
    if ($PSBoundParameters.ContainsKey("RunnerToolCache")) {
        if (-not $RuntimeOnly -or [string]::IsNullOrWhiteSpace($RunnerToolCache) -or
            -not [IO.Path]::IsPathRooted($RunnerToolCache)) {
            throw "The Runner Python bootstrap requires an absolute RUNNER_TOOL_CACHE directory."
        }
        $ManagedRuntimeRoot = Join-Path ([IO.Path]::GetFullPath($RunnerToolCache)) "PersonalAIAssistant"
    }
    $ToolsRoot = Join-Path $ManagedRuntimeRoot "tools"
    $PythonInstallRoot = Join-Path $ManagedRuntimeRoot "python"
    $CacheRoot = Join-Path $ManagedRuntimeRoot "cache"
    $UvPath = Join-Path $ToolsRoot "uv.exe"

    $useRunnerPackage = $CheckRunnerRuntime -or $PSBoundParameters.ContainsKey("RunnerPythonArchive")
    if ($useRunnerPackage) {
        if (-not $PSBoundParameters.ContainsKey("RunnerToolCache") -or (Get-UvTarget) -ne "x86_64-pc-windows-msvc") {
            throw "The Engineering Runner Python package requires an x64 Windows Runner tool cache."
        }
        $RunnerPackage = Get-Content -LiteralPath (Join-Path $Root "scripts\engineering_runner_python.json") -Raw |
            ConvertFrom-Json
        $RunnerPythonRoot = Join-Path $ManagedRuntimeRoot ("runner-python-" + $RunnerPackage.sha256.Substring(0, 16))
        $RunnerPython = Join-Path $RunnerPythonRoot "python.exe"
        if ($CheckRunnerRuntime) {
            if (Test-RunnerPython $RunnerPython) { exit 0 }
            exit 3
        }
    }

    $selectedVenv = Join-Path $Root ".venv"
    if ((Test-Path -LiteralPath $selectedVenv) -and -not (Test-UsablePythonEnvironment $selectedVenv)) {
        $selectedVenv = Join-Path $RuntimeRoot "venvs\python-3.11"
        if ((Test-Path -LiteralPath $selectedVenv) -and -not (Test-UsablePythonEnvironment $selectedVenv)) {
            throw "The existing project and managed virtual environments are not usable. They were preserved; inspect them before choosing another runtime directory."
        }
    }

    if (-not (Test-UsablePythonEnvironment $selectedVenv)) {
        if ($useRunnerPackage) {
            if (Test-RunnerPython $RunnerPython) {
                Write-Host "Reusing locally managed Python $($RunnerPackage.version)."
            } else {
                Install-RunnerPython
            }
            $managedPython = $RunnerPython
        } else {
            New-Item -ItemType Directory -Path $ToolsRoot, $PythonInstallRoot, $CacheRoot -Force | Out-Null
            $env:UV_PYTHON_INSTALL_DIR = $PythonInstallRoot
            $env:UV_CACHE_DIR = $CacheRoot
            $env:UV_PYTHON_PREFERENCE = "only-managed"
            $env:UV_NO_PROGRESS = "1"
            Initialize-VerifiedUv

            Write-Host "Preparing project-local managed Python 3.11 and virtual environment ..."
            $managedPython = Find-LocalManagedPython
            if (-not $managedPython) {
                & $UvPath python install --no-bin --no-registry 3.11
                if ($LASTEXITCODE -ne 0) { throw "Could not install the managed Python 3.11 runtime." }
                $managedPython = Find-LocalManagedPython
            } else {
                Write-Host "Reusing locally managed Python 3.11."
            }
            if (-not $managedPython) {
                throw "Could not resolve the installed managed Python 3.11 executable."
            }
        }
        & $managedPython -m venv $selectedVenv
        if ($LASTEXITCODE -ne 0) { throw "Could not create the project virtual environment." }
    }

    $python = Join-Path $selectedVenv "Scripts\python.exe"
    if (-not (Test-UsablePythonEnvironment $selectedVenv)) {
        throw "The selected Python environment failed its Python 3.11+ and pip validation."
    }

    $env:PAA_VENV_DIR = $selectedVenv
    if ($RuntimeOnly) {
        Write-Host "Managed Python runtime bootstrap passed: $(& $python --version 2>&1)"
        exit 0
    }

    $applicationArguments = @()
    if ($Dev) { $applicationArguments += "--dev" }
    if ($CheckOnly) { $applicationArguments += "--check-only" }

    Write-Host "Runtime directory: $RuntimeRoot"
    Write-Host "Python environment: $selectedVenv"
    & $python (Join-Path $Root "run.py") @applicationArguments
    exit $LASTEXITCODE
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 2
}
