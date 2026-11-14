[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Model,
    [ValidateRange(30, 900)]
    [int]$TimeoutSeconds = 600,
    [switch]$VerifyOnly
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot "ollama_context\OllamaContext.psm1") -Force

function Assert-SelectedContext {
    param(
        [Parameter(Mandatory = $true)][long]$Selected,
        [Parameter(Mandatory = $true)][long]$Maximum
    )
    if ($Selected -le 0 -or $Selected -gt $Maximum) {
        throw "Selected context $Selected is outside the supported Codex range for model maximum $Maximum."
    }
}

if ($VerifyOnly) {
    # Verification is intentionally read-only: do not run another inference or capability probe.
    $codexModel = $Model
    $selectedContext = 0L
    if (-not [int64]::TryParse([string]$env:AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH, [ref]$selectedContext) -or $selectedContext -le 0) {
        throw "The selected Codex context was not persisted as a positive verified allocation for runtime verification."
    }
    $modelInfo = Get-ModelInfo -ModelName $codexModel
    if ($null -eq $modelInfo) {
        throw "Persistent Codex model '$codexModel' is no longer installed."
    }
    $maximum = Get-MaximumContext -Info $modelInfo
    Assert-SelectedContext -Selected $selectedContext -Maximum $maximum
    $savedContext = [regex]::Match(
        [string]$modelInfo.modelfile,
        '(?im)^\s*PARAMETER\s+num_ctx\s+(\d+)\s*$'
    )
    if (-not $savedContext.Success -or [int64]$savedContext.Groups[1].Value -ne $selectedContext) {
        throw "Persistent model '$codexModel' does not retain the selected num_ctx $selectedContext."
    }
} else {
    if ($Model -notmatch '^[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9._-]+)?$') {
        throw "Model name contains unsupported characters; refusing to construct a persistent Codex model variant."
    }
    if ($Model -match '(?i)-codex-ctx[0-9]+$') {
        throw "The selected model is an internal Codex context variant. Select the original Ollama model to prevent nested variants."
    }

    $baseModel = $Model
    $baseInfo = Get-ModelInfo -ModelName $baseModel
    if ($null -eq $baseInfo) {
        throw "Selected Ollama model '$baseModel' is not installed."
    }
    # Metadata-only capability validation happens before model creation. If Ollama
    # omits capability metadata, defer the one live probe until the persistent
    # variant exists so that the probe also warms the exact model Codex will use.
    $null = Assert-ToolCallingCapability -Info $baseInfo -ModelName $baseModel -DeferLiveProbe
    $maximum = Get-MaximumContext -Info $baseInfo
    if ($maximum -le 0) {
        throw "Selected model maximum context is not a positive verified value."
    }

    $requestedContext = $null
    $manualContext = [string]$env:AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH
    if (-not [string]::IsNullOrWhiteSpace($manualContext)) {
        $parsedContext = 0L
        if (-not [int64]::TryParse($manualContext, [ref]$parsedContext) -or $parsedContext -le 0) {
            throw "AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH must be a positive integer."
        }
        $requestedContext = [Nullable[long]]$parsedContext
        $selectionMode = "requested-and-safely-clamped"
    } else {
        $selectionMode = "automatic"
    }

    $contextSelection = Get-AutomaticContext -Info $baseInfo -ModelName $baseModel -RequestedContext $requestedContext
    $selectedContext = [int64]$contextSelection.selected_context_tokens
    Assert-SelectedContext -Selected $selectedContext -Maximum $maximum

    Write-Host "Model maximum context: $($contextSelection.maximum_context_tokens) tokens"
    Write-Host "Hardware-safe context ceiling: $($contextSelection.hardware_maximum_context_tokens) tokens"
    Write-Host "Available physical RAM: $($contextSelection.available_memory_bytes) bytes"
    Write-Host "Installed model weight size: $($contextSelection.model_weight_bytes) bytes"
    Write-Host "Estimated KV cache: $($contextSelection.kv_cache_bytes_per_token) bytes/token"
    Write-Host "Reserved system RAM: $($contextSelection.reserve_bytes) bytes"
    Write-Host "Model/runtime overhead budget: $($contextSelection.overhead_bytes) bytes"
    if ($null -ne $requestedContext) {
        Write-Host "Requested context: $requestedContext tokens"
    }
    Write-Host "Final context: min(requested, model maximum, safe hardware capacity, Codex catalog maximum) = $selectedContext tokens"

    $codexModel = "$baseModel-codex-ctx$selectedContext"
    $existingInfo = Get-ModelInfo -ModelName $codexModel
    $existingContext = [regex]::Match(
        [string]$existingInfo.modelfile,
        '(?im)^\s*PARAMETER\s+num_ctx\s+(\d+)\s*$'
    )
    $existingMatch = $existingContext.Success -and [int64]$existingContext.Groups[1].Value -eq $selectedContext

    if (-not $existingMatch) {
        $createBody = @{
            model = $codexModel
            from = $baseModel
            parameters = @{ num_ctx = $selectedContext }
            stream = $false
        } | ConvertTo-Json -Depth 5 -Compress
        try {
            $null = Invoke-OllamaApi -Path "/api/create" -Method Post -Body $createBody -TimeoutSec $TimeoutSeconds
        } catch {
            throw "Ollama could not create persistent Codex model '$codexModel' at context $selectedContext. $($_.Exception.Message)"
        }
    }

    $modelInfo = Get-ModelInfo -ModelName $codexModel
    if ($null -eq $modelInfo) {
        throw "Persistent Codex model '$codexModel' was not found after creation."
    }
    $savedContext = [regex]::Match(
        [string]$modelInfo.modelfile,
        '(?im)^\s*PARAMETER\s+num_ctx\s+(\d+)\s*$'
    )
    if (-not $savedContext.Success -or [int64]$savedContext.Groups[1].Value -ne $selectedContext) {
        throw "Persistent model '$codexModel' does not declare the selected num_ctx $selectedContext."
    }

    # The live structured-tool probe is also the single model warmup when
    # capability metadata is absent. Do not unload and cold-load the same model.
    $probePerformed = Assert-ToolCallingCapability -Info $modelInfo -ModelName $codexModel
    if (-not $probePerformed) {
        Start-OllamaModelWarmup -ModelName $codexModel -ContextTokens $selectedContext -TimeoutSeconds $TimeoutSeconds
    }

    if ($env:GITHUB_ENV) {
        Add-Content -LiteralPath $env:GITHUB_ENV -Value "AUTONOMOUS_ENGINEERING_MODEL=$codexModel" -Encoding utf8
        Add-Content -LiteralPath $env:GITHUB_ENV -Value "AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH=$selectedContext" -Encoding utf8
    } else {
        $env:AUTONOMOUS_ENGINEERING_MODEL = $codexModel
        $env:AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH = [string]$selectedContext
    }
    Write-Host "Context selection mode: $selectionMode"
}

$allocated = Get-AllocatedContext -ModelName $codexModel
Write-Host "Ollama model passed to Codex: $codexModel"
Write-Host "Persistent Codex model: $codexModel"
Write-Host "Model maximum context: $maximum tokens"
Write-Host "Selected Codex context: $selectedContext tokens"
Write-Host "Ollama allocated runtime context: $allocated tokens"
if ($allocated -ne $selectedContext) {
    throw "Ollama allocated $allocated tokens for '$codexModel', but the verified selection is exactly $selectedContext. No Codex task may proceed."
}
Write-Host "OLLAMA_RUNTIME_CONTEXT_OK"
