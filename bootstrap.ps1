[CmdletBinding()]
param(
    [switch]$Dev,
    [switch]$CheckOnly,
    [switch]$RuntimeOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = [System.IO.Path]::GetFullPath($PSScriptRoot)
$RuntimeRoot = Join-Path $Root ".runtime"
$ToolsRoot = Join-Path $RuntimeRoot "tools"
$PythonInstallRoot = Join-Path $RuntimeRoot "python"
$CacheRoot = Join-Path $RuntimeRoot "cache"
$UvVersion = "0.13.9"
$UvPath = Join-Path $ToolsRoot "uv.exe"

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

function Install-VerifiedUv([string]$Target) {
    $asset = $UvAssets[$Target]
    $downloadUrl = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-$Target.zip"
    $downloadRoot = Join-Path $RuntimeRoot ("download-" + [guid]::NewGuid().ToString("N"))
    $archive = Join-Path $downloadRoot "uv.zip"
    $extractRoot = Join-Path $downloadRoot "extract"
    $stagedUv = Join-Path $downloadRoot "uv.exe"

    New-Item -ItemType Directory -Path $downloadRoot, $extractRoot, $ToolsRoot -Force | Out-Null
    try {
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $downloadUrl -OutFile $archive -UseBasicParsing -TimeoutSec 180
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

try {
    New-Item -ItemType Directory -Path $RuntimeRoot, $ToolsRoot, $PythonInstallRoot, $CacheRoot -Force | Out-Null
    $env:UV_PYTHON_INSTALL_DIR = $PythonInstallRoot
    $env:UV_CACHE_DIR = $CacheRoot
    $env:UV_PYTHON_PREFERENCE = "only-managed"
    $env:UV_NO_PROGRESS = "1"

    $target = Get-UvTarget
    $needsUv = $true
    if (Test-Path -LiteralPath $UvPath -PathType Leaf) {
        $version = (& $UvPath --version 2>$null | Out-String).Trim()
        if ($version -match ('^uv\s+' + [regex]::Escape($UvVersion) + '(\s|$)')) { $needsUv = $false }
    }
    if ($needsUv) {
        Write-Host "Installing verified uv $UvVersion for $target ..."
        Install-VerifiedUv -Target $target
    }

    $selectedVenv = Join-Path $Root ".venv"
    if ((Test-Path -LiteralPath $selectedVenv) -and -not (Test-UsablePythonEnvironment $selectedVenv)) {
        $selectedVenv = Join-Path $RuntimeRoot "venvs\python-3.11"
        if ((Test-Path -LiteralPath $selectedVenv) -and -not (Test-UsablePythonEnvironment $selectedVenv)) {
            throw "The existing project and managed virtual environments are not usable. They were preserved; inspect them before choosing another runtime directory."
        }
    }

    if (-not (Test-UsablePythonEnvironment $selectedVenv)) {
        Write-Host "Preparing project-local managed Python 3.11 and virtual environment ..."
        & $UvPath python install 3.11
        if ($LASTEXITCODE -ne 0) { throw "Could not install the managed Python 3.11 runtime." }
        & $UvPath venv --seed --python 3.11 $selectedVenv
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
    Write-Error $_
    exit 2
}
