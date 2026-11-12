param([string]$SelectedModel)
$ErrorActionPreference = "Stop"
$model = if (-not [string]::IsNullOrWhiteSpace($SelectedModel)) {
    $SelectedModel
} elseif ($env:AUTONOMOUS_ENGINEERING_MODEL) {
    $env:AUTONOMOUS_ENGINEERING_MODEL
} else {
    ""
}
$checks = @()
$modelIsContextVariant = $model -match '(?i)-codex-ctx[0-9]+$'

$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
$runnerIsAdmin = $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
$checks += [pscustomobject]@{
    Component = "Runner privilege"
    Ready = -not $runnerIsAdmin
    Detail = if ($runnerIsAdmin) { "Do not run the autonomous Runner as a local administrator" } else { "Non-administrator service account" }
}

$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$nodeReady = [bool]$nodeCommand
$nodeVersion = "Node.js not found in Runner account PATH"
if ($nodeReady) {
    $nodeVersionOutput = & node --version 2>$null
    $nodeExitCode = $LASTEXITCODE
    $nodeVersion = [string](@($nodeVersionOutput)[0])
    $nodeReady = ($nodeExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($nodeVersion))
}
$checks += [pscustomobject]@{ Component = "Node.js"; Ready = $nodeReady; Detail = $nodeVersion }

$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
$npmReady = [bool]$npmCommand
$npmPrefix = ""
$npmVersion = "npm.cmd not found in Runner account PATH"
if ($npmReady) {
    $npmVersionOutput = & $npmCommand.Source --version 2>$null
    $npmVersionExitCode = $LASTEXITCODE
    $npmVersion = [string](@($npmVersionOutput)[0])
    $npmReady = ($npmVersionExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($npmVersion))
    if ($npmReady) {
        $npmPrefixOutput = & $npmCommand.Source config get prefix 2>$null
        $npmPrefixExitCode = $LASTEXITCODE
        $npmPrefix = [string](@($npmPrefixOutput)[0])
        $npmPrefix = $npmPrefix.Trim()
        $npmReady = ($npmPrefixExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($npmPrefix))
    }
}
$checks += [pscustomobject]@{ Component = "npm"; Ready = $npmReady; Detail = if ($npmReady) { "$npmVersion; global prefix: $npmPrefix" } else { $npmVersion } }

$env:PATH = "$npmPrefix;$env:PATH"
$codexCommand = Get-Command codex.cmd -ErrorAction SilentlyContinue
$codexReady = [bool]$codexCommand
$codexVersion = "Codex CLI will be installed automatically by the first autonomous workflow run."
if ($codexReady) {
    $codexVersionOutput = & $codexCommand.Source --version 2>$null
    $codexExitCode = $LASTEXITCODE
    $codexVersion = [string](@($codexVersionOutput)[0])
    $codexReady = ($codexExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($codexVersion))
}
$checks += [pscustomobject]@{
    Component = "Codex CLI"
    Ready = $codexReady -or $npmReady
    Detail = if ($codexReady) { $codexVersion } elseif ($npmReady) { "Codex CLI will be installed by the first autonomous workflow run." } else { "Install Node.js/npm; the workflow installs Codex under this service account." }
}

$ollamaCommand = Get-Command ollama.exe -ErrorAction SilentlyContinue
$ollamaExecutable = if ($ollamaCommand) { $ollamaCommand.Source } else { $null }
if (-not $ollamaExecutable) {
    try {
        $ollamaProcess = Get-CimInstance Win32_Process -Filter "Name='ollama.exe'" -ErrorAction Stop |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_.ExecutablePath) } |
            Select-Object -First 1
        if ($ollamaProcess -and (Test-Path -LiteralPath $ollamaProcess.ExecutablePath -PathType Leaf)) {
            $ollamaExecutable = [string]$ollamaProcess.ExecutablePath
        }
    } catch {}
}
$ollamaCliReady = -not [string]::IsNullOrWhiteSpace($ollamaExecutable)
$ollamaVersion = "Ollama CLI not found in PATH or active ollama.exe process"
if ($ollamaCliReady) {
    $ollamaVersionOutput = & $ollamaExecutable --version 2>$null
    $ollamaExitCode = $LASTEXITCODE
    $ollamaVersion = [string](@($ollamaVersionOutput)[0])
    $ollamaCliReady = ($ollamaExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($ollamaVersion))
}
$ollamaOnline = $false
$modelInstalled = $false
$modelContextLength = $null
$modelSupportsTools = $false
try {
    $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 4 -MaximumRedirection 0
    $ollamaOnline = $true
    $modelInstalled = @($tags.models | ForEach-Object { $_.name }) -contains $model
    if ($modelInstalled) {
        $modelInfo = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/show" -Method Post -ContentType "application/json" -Body (@{ model = $model; verbose = $true } | ConvertTo-Json -Compress) -TimeoutSec 8 -MaximumRedirection 0
        $architecture = [string]$modelInfo.details.family
        $contextKey = $architecture + ".context_length"
        $rawContext = $modelInfo.model_info.PSObject.Properties[$contextKey]
        $parsedContext = 0L
        if ($rawContext -and [int64]::TryParse([string]$rawContext.Value, [ref]$parsedContext) -and $parsedContext -ge 512) {
            $modelContextLength = $parsedContext
        }
        $modelSupportsTools = @($modelInfo.capabilities | ForEach-Object { [string]$_ }) -contains "tools"
    }
} catch {}
$checks += [pscustomobject]@{ Component = "Ollama CLI"; Ready = $ollamaCliReady; Detail = if ($ollamaCliReady) { $ollamaVersion } else { "Install Ollama CLI or add it to the Runner service PATH" } }
$checks += [pscustomobject]@{ Component = "Ollama server"; Ready = $ollamaOnline; Detail = if ($ollamaOnline) { "127.0.0.1:11434" } else { "Local Ollama endpoint is unavailable" } }
$checks += [pscustomobject]@{ Component = "Saved model selection"; Ready = (-not [string]::IsNullOrWhiteSpace($model)); Detail = if ($model) { $model } else { "Select and save an installed Ollama model in /autonomous-engineering" } }
$checks += [pscustomobject]@{ Component = "Base model selection"; Ready = -not $modelIsContextVariant; Detail = if ($modelIsContextVariant) { "Generated Codex context variants cannot be selected as base models" } else { "Original Ollama model selected" } }
$checks += [pscustomobject]@{ Component = "Configured model"; Ready = $modelInstalled; Detail = if ($modelInstalled) { $model } elseif (-not $model) { "No model has been selected and saved" } else { "Saved model is not installed in Ollama" } }
$checks += [pscustomobject]@{ Component = "Model context metadata"; Ready = ($null -ne $modelContextLength -and $modelContextLength -ge 64000); Detail = if ($null -eq $modelContextLength) { "Ollama /api/show did not report a valid architecture context_length" } elseif ($modelContextLength -lt 64000) { "$modelContextLength tokens maximum; Codex requires at least 64000" } else { "$modelContextLength tokens maximum; Codex minimum satisfied" } }
$checks += [pscustomobject]@{ Component = "Model tool calling"; Ready = $modelSupportsTools; Detail = if ($modelSupportsTools) { "Ollama advertises tools capability" } else { "Selected model does not advertise tools capability required by Codex" } }

$runner = Get-Process -Name "Runner.Listener" -ErrorAction SilentlyContinue | Select-Object -First 1
$runnerReady = [bool]$runner
$checks += [pscustomobject]@{ Component = "GitHub Runner"; Ready = $runnerReady; Detail = if ($runnerReady) { "Listener process active" } else { "Runner.Listener is not running in this account" } }

$progressPath = Join-Path $env:ProgramData "PersonalAIAssistant\autonomous-engineering"
$progressReady = $false
$probe = Join-Path $progressPath ".runner-write-check-$PID.tmp"
try {
    if (Test-Path -LiteralPath $progressPath -PathType Container) {
        [System.IO.File]::WriteAllText($probe, "ok")
        Remove-Item -LiteralPath $probe -Force
        $progressReady = $true
    }
} catch {
    if (Test-Path -LiteralPath $probe) {
        Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
    }
}
$checks += [pscustomobject]@{ Component = "Shared progress storage"; Ready = $progressReady; Detail = if ($progressReady) { "Runner account can write to $progressPath" } else { "Run configure_autonomous_engineering_progress.ps1 with the exact Runner service account" } }

$checks | Format-Table -AutoSize
if (@($checks | Where-Object { -not $_.Ready }).Count -gt 0) {
    exit 1
}
Write-Host "Runner-account prerequisites are ready. No GitHub CLI login is required in this account."
