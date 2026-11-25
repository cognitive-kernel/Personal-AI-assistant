param([string]$SelectedModel)
$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot "AutonomousOllamaRuntime.psm1") -Force
$model = if (-not [string]::IsNullOrWhiteSpace($SelectedModel)) {
    $SelectedModel
} elseif ($env:AUTONOMOUS_ENGINEERING_MODEL) {
    $env:AUTONOMOUS_ENGINEERING_MODEL
} else {
    ""
}
$checks = @()
$modelIsContextVariant = $model -match '(?i)-codex-ctx([0-9]+)?(-paa-[0-9]+-[0-9]+-[a-f0-9]{12})?

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
    $nodeVersion = (& node --version 2>$null | Select-Object -First 1)
    $nodeReady = ($LASTEXITCODE -eq 0 -and [bool]$nodeVersion)
}
$checks += [pscustomobject]@{ Component = "Node.js"; Ready = $nodeReady; Detail = $nodeVersion }

$npmCommand = Get-Command npm -ErrorAction SilentlyContinue
$npmReady = [bool]$npmCommand
$npmPrefix = ""
$npmVersion = "npm not found in Runner account PATH"
if ($npmReady) {
    $npmVersion = (& npm --version 2>$null | Select-Object -First 1)
    $npmReady = ($LASTEXITCODE -eq 0 -and [bool]$npmVersion)
    if ($npmReady) {
        $npmPrefix = (& npm config get prefix 2>$null | Select-Object -First 1).Trim()
        $npmReady = ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($npmPrefix))
    }
}
$checks += [pscustomobject]@{ Component = "npm"; Ready = $npmReady; Detail = if ($npmReady) { "$npmVersion; global prefix: $npmPrefix" } else { $npmVersion } }

$env:PATH = "$npmPrefix;$env:PATH"
$codexCommand = Get-Command codex -ErrorAction SilentlyContinue
$codexReady = [bool]$codexCommand
$codexVersion = "Codex CLI will be installed automatically by the first autonomous workflow run."
if ($codexReady) {
    $codexVersion = (& codex --version 2>$null | Select-Object -First 1)
    $codexReady = ($LASTEXITCODE -eq 0 -and [bool]$codexVersion)
}
$checks += [pscustomobject]@{
    Component = "Codex CLI"
    Ready = $codexReady -or $npmReady
    Detail = if ($codexReady) { $codexVersion } elseif ($npmReady) { $codexVersion } else { "Install Node.js/npm; the workflow installs Codex under this service account." }
}

$ollamaOnline = $false
$modelInstalled = $false
$modelContextLength = $null
$modelSupportsTools = $false
$toolVerificationDetail = "Tool calling has not been verified."
try {
    $tags = @(Get-AutonomousOllamaModelCatalog -TimeoutSeconds 4)
    $ollamaOnline = $true
    $modelInstalled = Test-AutonomousOllamaModelInstalled -Model $model -Catalog $tags
    if ($modelInstalled) {
        $modelInfo = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/show" -Method Post -ContentType "application/json" -Body (@{ model = $model; verbose = $true } | ConvertTo-Json -Compress) -TimeoutSec 8 -MaximumRedirection 0
        $architecture = [string]$modelInfo.details.family
        $contextKey = $architecture + ".context_length"
        $rawContext = $modelInfo.model_info.PSObject.Properties[$contextKey]
        if (-not $rawContext) {
            $declaredArchitecture = $modelInfo.model_info.PSObject.Properties["general.architecture"]
            if ($declaredArchitecture) {
                $architecture = [string]$declaredArchitecture.Value
                $contextKey = $architecture + ".context_length"
                $rawContext = $modelInfo.model_info.PSObject.Properties[$contextKey]
            }
        }
        $parsedContext = 0L
        if ($rawContext -and [int64]::TryParse([string]$rawContext.Value, [ref]$parsedContext) -and $parsedContext -gt 0) {
            $modelContextLength = $parsedContext
        }
        $capabilitiesProperty = $modelInfo.PSObject.Properties["capabilities"]
        if ($capabilitiesProperty -and $null -ne $capabilitiesProperty.Value) {
            $modelSupportsTools = @($capabilitiesProperty.Value | ForEach-Object { [string]$_ }) -contains "tools"
            $toolVerificationDetail = if ($modelSupportsTools) { "Ollama model capabilities metadata confirms tools" } else { "Ollama model capabilities metadata explicitly omits tools" }
        } else {
            $toolProbeScript = Join-Path $PSScriptRoot "verify_ollama_tools.py"
            & python $toolProbeScript --model $model 2>&1 | Out-Null
            $modelSupportsTools = ($LASTEXITCODE -eq 0)
            $toolVerificationDetail = if ($modelSupportsTools) { "Live Ollama tool-call probe passed" } else { "Live Ollama tool-call probe failed" }
        }
    }
} catch {}
$checks += [pscustomobject]@{ Component = "Ollama server"; Ready = $ollamaOnline; Detail = if ($ollamaOnline) { "127.0.0.1:11434" } else { "Local Ollama endpoint is unavailable" } }
$checks += [pscustomobject]@{ Component = "Saved model selection"; Ready = (-not [string]::IsNullOrWhiteSpace($model)); Detail = if ($model) { $model } else { "Select and save an installed Ollama model in /autonomous-engineering" } }
$checks += [pscustomobject]@{ Component = "Base model selection"; Ready = -not $modelIsContextVariant; Detail = if ($modelIsContextVariant) { "Generated Codex context variants cannot be selected as base models" } else { "Original Ollama model selected" } }
$checks += [pscustomobject]@{ Component = "Configured model"; Ready = $modelInstalled; Detail = if ($modelInstalled) { $model } elseif (-not $model) { "No model has been selected and saved" } else { "Saved model is not installed in Ollama" } }
$checks += [pscustomobject]@{ Component = "Model context metadata"; Ready = ($null -ne $modelContextLength -and $modelContextLength -gt 0); Detail = if ($null -eq $modelContextLength) { "Ollama /api/show did not report a valid architecture context_length" } elseif ($modelContextLength -le 0) { "$modelContextLength tokens maximum; positive model context metadata verified" } else { "$modelContextLength tokens maximum; positive model context metadata verified" } }
$checks += [pscustomobject]@{ Component = "Model tool calling"; Ready = $modelSupportsTools; Detail = $toolVerificationDetail }

$runner = Get-Process -Name "Runner.Listener" -ErrorAction SilentlyContinue | Select-Object -First 1
$runnerReady = [bool]$runner
$checks += [pscustomobject]@{ Component = "GitHub Runner"; Ready = $runnerReady; Detail = if ($runnerReady) { "Listener process active" } else { "Runner.Listener is not running in this account" } }

$progressPath = $null
$progressReady = $false
$brokerReady = $false
try {
    Import-Module (Join-Path $PSScriptRoot "AutonomousWindowsPaths.psm1") -Force
    $machineStatePath = [Environment]::GetEnvironmentVariable("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "Machine")
    $processStatePath = $env:AUTONOMOUS_ENGINEERING_PROGRESS_DIR
    if (-not [string]::IsNullOrWhiteSpace($processStatePath) -and [string]::IsNullOrWhiteSpace($machineStatePath)) {
        throw "Process-scoped state path is not accepted."
    }
    $progressPath = Resolve-AutonomousStateDirectory -ConfiguredPath $machineStatePath -ProgramDataPath $env:ProgramData
    $probe = Join-Path $progressPath (".runner-write-check-" + [guid]::NewGuid().ToString("N") + ".tmp")
    [System.IO.File]::WriteAllText($probe, "ok")
    Remove-Item -LiteralPath $probe -Force -ErrorAction Stop
    $progressReady = $true
} catch {
    $progressReady = $false
}
$checks += [pscustomobject]@{
    Component = "Runner shared-state write access"
    Ready = $progressReady
    Detail = if ($progressReady) { "Runner role can write to the machine state root" } else { "Runner identity must have Modify access to the machine state root" }
}

try {
    $brokerService = Get-Service -Name "PersonalAIAssistantStateBroker" -ErrorAction Stop
    if ($brokerService.Status -eq "Running") {
        Import-Module (Join-Path $PSScriptRoot "AutonomousStateBroker.psm1") -Force
        $brokerHealth = Invoke-AutonomousStateBroker -Method GET -Path "/v1/health" -TimeoutMilliseconds 4000
        $brokerReady = ($brokerHealth.ready -eq $true -and $brokerHealth.schema_version -eq 1)
    }
} catch {}
$checks += [pscustomobject]@{
    Component = "Machine state broker"
    Ready = $brokerReady
    Detail = if ($brokerReady) { "Local authenticated pipe is available" } else { "Install or start PersonalAIAssistantStateBroker" }
}

$checks | Format-Table -AutoSize
if (@($checks | Where-Object { -not $_.Ready }).Count -gt 0) {
    exit 1
}
Write-Host "Runner-account prerequisites are ready. No GitHub CLI login is required in this account."


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
    $nodeVersion = (& node --version 2>$null | Select-Object -First 1)
    $nodeReady = ($LASTEXITCODE -eq 0 -and [bool]$nodeVersion)
}
$checks += [pscustomobject]@{ Component = "Node.js"; Ready = $nodeReady; Detail = $nodeVersion }

$npmCommand = Get-Command npm -ErrorAction SilentlyContinue
$npmReady = [bool]$npmCommand
$npmPrefix = ""
$npmVersion = "npm not found in Runner account PATH"
if ($npmReady) {
    $npmVersion = (& npm --version 2>$null | Select-Object -First 1)
    $npmReady = ($LASTEXITCODE -eq 0 -and [bool]$npmVersion)
    if ($npmReady) {
        $npmPrefix = (& npm config get prefix 2>$null | Select-Object -First 1).Trim()
        $npmReady = ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($npmPrefix))
    }
}
$checks += [pscustomobject]@{ Component = "npm"; Ready = $npmReady; Detail = if ($npmReady) { "$npmVersion; global prefix: $npmPrefix" } else { $npmVersion } }

$env:PATH = "$npmPrefix;$env:PATH"
$codexCommand = Get-Command codex -ErrorAction SilentlyContinue
$codexReady = [bool]$codexCommand
$codexVersion = "Codex CLI will be installed automatically by the first autonomous workflow run."
if ($codexReady) {
    $codexVersion = (& codex --version 2>$null | Select-Object -First 1)
    $codexReady = ($LASTEXITCODE -eq 0 -and [bool]$codexVersion)
}
$checks += [pscustomobject]@{
    Component = "Codex CLI"
    Ready = $codexReady -or $npmReady
    Detail = if ($codexReady) { $codexVersion } elseif ($npmReady) { $codexVersion } else { "Install Node.js/npm; the workflow installs Codex under this service account." }
}

$ollamaOnline = $false
$modelInstalled = $false
$modelContextLength = $null
$modelSupportsTools = $false
$toolVerificationDetail = "Tool calling has not been verified."
try {
    $tags = @(Get-AutonomousOllamaModelCatalog -TimeoutSeconds 4)
    $ollamaOnline = $true
    $modelInstalled = Test-AutonomousOllamaModelInstalled -Model $model -Catalog $tags
    if ($modelInstalled) {
        $modelInfo = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/show" -Method Post -ContentType "application/json" -Body (@{ model = $model; verbose = $true } | ConvertTo-Json -Compress) -TimeoutSec 8 -MaximumRedirection 0
        $architecture = [string]$modelInfo.details.family
        $contextKey = $architecture + ".context_length"
        $rawContext = $modelInfo.model_info.PSObject.Properties[$contextKey]
        if (-not $rawContext) {
            $declaredArchitecture = $modelInfo.model_info.PSObject.Properties["general.architecture"]
            if ($declaredArchitecture) {
                $architecture = [string]$declaredArchitecture.Value
                $contextKey = $architecture + ".context_length"
                $rawContext = $modelInfo.model_info.PSObject.Properties[$contextKey]
            }
        }
        $parsedContext = 0L
        if ($rawContext -and [int64]::TryParse([string]$rawContext.Value, [ref]$parsedContext) -and $parsedContext -gt 0) {
            $modelContextLength = $parsedContext
        }
        $capabilitiesProperty = $modelInfo.PSObject.Properties["capabilities"]
        if ($capabilitiesProperty -and $null -ne $capabilitiesProperty.Value) {
            $modelSupportsTools = @($capabilitiesProperty.Value | ForEach-Object { [string]$_ }) -contains "tools"
            $toolVerificationDetail = if ($modelSupportsTools) { "Ollama model capabilities metadata confirms tools" } else { "Ollama model capabilities metadata explicitly omits tools" }
        } else {
            $toolProbeScript = Join-Path $PSScriptRoot "verify_ollama_tools.py"
            & python $toolProbeScript --model $model 2>&1 | Out-Null
            $modelSupportsTools = ($LASTEXITCODE -eq 0)
            $toolVerificationDetail = if ($modelSupportsTools) { "Live Ollama tool-call probe passed" } else { "Live Ollama tool-call probe failed" }
        }
    }
} catch {}
$checks += [pscustomobject]@{ Component = "Ollama server"; Ready = $ollamaOnline; Detail = if ($ollamaOnline) { "127.0.0.1:11434" } else { "Local Ollama endpoint is unavailable" } }
$checks += [pscustomobject]@{ Component = "Saved model selection"; Ready = (-not [string]::IsNullOrWhiteSpace($model)); Detail = if ($model) { $model } else { "Select and save an installed Ollama model in /autonomous-engineering" } }
$checks += [pscustomobject]@{ Component = "Base model selection"; Ready = -not $modelIsContextVariant; Detail = if ($modelIsContextVariant) { "Generated Codex context variants cannot be selected as base models" } else { "Original Ollama model selected" } }
$checks += [pscustomobject]@{ Component = "Configured model"; Ready = $modelInstalled; Detail = if ($modelInstalled) { $model } elseif (-not $model) { "No model has been selected and saved" } else { "Saved model is not installed in Ollama" } }
$checks += [pscustomobject]@{ Component = "Model context metadata"; Ready = ($null -ne $modelContextLength -and $modelContextLength -gt 0); Detail = if ($null -eq $modelContextLength) { "Ollama /api/show did not report a valid architecture context_length" } elseif ($modelContextLength -le 0) { "$modelContextLength tokens maximum; positive model context metadata verified" } else { "$modelContextLength tokens maximum; positive model context metadata verified" } }
$checks += [pscustomobject]@{ Component = "Model tool calling"; Ready = $modelSupportsTools; Detail = $toolVerificationDetail }

$runner = Get-Process -Name "Runner.Listener" -ErrorAction SilentlyContinue | Select-Object -First 1
$runnerReady = [bool]$runner
$checks += [pscustomobject]@{ Component = "GitHub Runner"; Ready = $runnerReady; Detail = if ($runnerReady) { "Listener process active" } else { "Runner.Listener is not running in this account" } }

$progressPath = $null
$progressReady = $false
$brokerReady = $false
try {
    Import-Module (Join-Path $PSScriptRoot "AutonomousWindowsPaths.psm1") -Force
    $machineStatePath = [Environment]::GetEnvironmentVariable("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "Machine")
    $processStatePath = $env:AUTONOMOUS_ENGINEERING_PROGRESS_DIR
    if (-not [string]::IsNullOrWhiteSpace($processStatePath) -and [string]::IsNullOrWhiteSpace($machineStatePath)) {
        throw "Process-scoped state path is not accepted."
    }
    $progressPath = Resolve-AutonomousStateDirectory -ConfiguredPath $machineStatePath -ProgramDataPath $env:ProgramData
    $probe = Join-Path $progressPath (".runner-write-check-" + [guid]::NewGuid().ToString("N") + ".tmp")
    [System.IO.File]::WriteAllText($probe, "ok")
    Remove-Item -LiteralPath $probe -Force -ErrorAction Stop
    $progressReady = $true
} catch {
    $progressReady = $false
}
$checks += [pscustomobject]@{
    Component = "Runner shared-state write access"
    Ready = $progressReady
    Detail = if ($progressReady) { "Runner role can write to the machine state root" } else { "Runner identity must have Modify access to the machine state root" }
}

try {
    $brokerService = Get-Service -Name "PersonalAIAssistantStateBroker" -ErrorAction Stop
    if ($brokerService.Status -eq "Running") {
        Import-Module (Join-Path $PSScriptRoot "AutonomousStateBroker.psm1") -Force
        $brokerHealth = Invoke-AutonomousStateBroker -Method GET -Path "/v1/health" -TimeoutMilliseconds 4000
        $brokerReady = ($brokerHealth.ready -eq $true -and $brokerHealth.schema_version -eq 1)
    }
} catch {}
$checks += [pscustomobject]@{
    Component = "Machine state broker"
    Ready = $brokerReady
    Detail = if ($brokerReady) { "Local authenticated pipe is available" } else { "Install or start PersonalAIAssistantStateBroker" }
}

$checks | Format-Table -AutoSize
if (@($checks | Where-Object { -not $_.Ready }).Count -gt 0) {
    exit 1
}
Write-Host "Runner-account prerequisites are ready. No GitHub CLI login is required in this account."
