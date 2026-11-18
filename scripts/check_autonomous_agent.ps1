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
$modelIsContextVariant = $model -match '(?i)-codex-ctx[0-9]+$'

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
$checks += [pscustomobject]@{ Component = "Ollama server"; Ready = $ollamaOnline; Detail = if ($ollamaOnline) { "127.0.0.1:11434" } else { "Start the local Ollama service" } }
$checks += [pscustomobject]@{ Component = "Saved model selection"; Ready = (-not [string]::IsNullOrWhiteSpace($model)); Detail = if ($model) { $model } else { "Select and save an installed Ollama model in /autonomous-engineering" } }
$checks += [pscustomobject]@{ Component = "Base model selection"; Ready = -not $modelIsContextVariant; Detail = if ($modelIsContextVariant) { "Generated Codex context variants cannot be selected as base models" } else { "Original Ollama model selected" } }
$checks += [pscustomobject]@{ Component = "Local model"; Ready = $modelInstalled; Detail = if ($modelInstalled) { $model } elseif (-not $model) { "No model has been selected and saved" } else { "Saved model is not installed in Ollama" } }
$checks += [pscustomobject]@{ Component = "Model context metadata"; Ready = ($null -ne $modelContextLength -and $modelContextLength -gt 0); Detail = if ($null -eq $modelContextLength) { "Ollama /api/show did not report a valid architecture context_length" } elseif ($modelContextLength -le 0) { "$modelContextLength tokens maximum; positive model context metadata verified" } else { "$modelContextLength tokens maximum; positive model context metadata verified" } }
$checks += [pscustomobject]@{ Component = "Model tool calling"; Ready = $modelSupportsTools; Detail = $toolVerificationDetail }

$ghCommand = Get-Command gh -ErrorAction SilentlyContinue
$ghReady = $false
if ($ghCommand) {
    gh auth status --hostname github.com *> $null
    $ghReady = ($LASTEXITCODE -eq 0)
}
$checks += [pscustomobject]@{ Component = "GitHub CLI"; Ready = $ghReady; Detail = if ($ghReady) { "Authenticated" } else { "Install gh and run gh auth login" } }

$runner = Get-Process -Name "Runner.Listener" -ErrorAction SilentlyContinue | Select-Object -First 1
$runnerReady = [bool]$runner
$checks += [pscustomobject]@{ Component = "GitHub Runner"; Ready = $runnerReady; Detail = if ($runnerReady) { "Listener process active" } else { "Register and start the self-hosted runner with label ollama" } }

$brokerReady = $false
$brokerDetail = "State broker service is not installed or reachable."
try {
    $brokerService = Get-Service -Name "PersonalAIAssistantStateBroker" -ErrorAction Stop
    if ($brokerService.Status -eq "Running") {
        Import-Module (Join-Path $PSScriptRoot "AutonomousStateBroker.psm1") -Force
        $brokerHealth = Invoke-AutonomousStateBroker -Method GET -Path "/v1/health" -TimeoutMilliseconds 4000
        $brokerReady = ($brokerHealth.ready -eq $true -and $brokerHealth.schema_version -eq 1)
        $brokerDetail = if ($brokerReady) { "Machine state broker is healthy; no app-user filesystem ACL is required" } else { "Machine state broker reports that protected shared storage is unavailable" }
    } else {
        $brokerDetail = "Machine state broker service is not running"
    }
} catch {
    $brokerDetail = "State broker check failed: $($_.Exception.GetType().Name)"
}
$checks += [pscustomobject]@{
    Component = "Shared progress storage"
    Ready = $brokerReady
    Detail = $brokerDetail
}

$checks | Format-Table -AutoSize
if (@($checks | Where-Object { -not $_.Ready }).Count -gt 0) {
    exit 1
}
Write-Host "Application-session prerequisites are ready. Any authenticated local app session uses the machine state broker; Runner file access is checked separately."
