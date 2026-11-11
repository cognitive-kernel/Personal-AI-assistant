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
$baseUrl = "http://127.0.0.1:11434"

function Get-ModelInfo {
    param([Parameter(Mandatory = $true)][string]$ModelName)
    $body = @{ model = $ModelName; verbose = $true } | ConvertTo-Json -Compress
    try {
        Invoke-RestMethod -Uri "$baseUrl/api/show" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 20
    } catch {
        throw "Could not inspect Ollama model '$ModelName'. $($_.Exception.Message)"
    }
}

function Get-MaximumContext {
    param([Parameter(Mandatory = $true)]$Info)
    $architecture = [string]$Info.details.family
    if ([string]::IsNullOrWhiteSpace($architecture)) {
        throw "Ollama did not report the selected model architecture."
    }
    $key = $architecture + ".context_length"
    $property = $Info.model_info.PSObject.Properties[$key]
    $maximum = 0L
    if (-not $property -or -not [int64]::TryParse([string]$property.Value, [ref]$maximum) -or $maximum -lt 512) {
        throw "Ollama did not publish a valid architecture-specific context limit ('$key')."
    }
    return $maximum
}

function Assert-ToolCallingCapability {
    param([Parameter(Mandatory = $true)]$Info)
    $capabilities = @($Info.capabilities | ForEach-Object { [string]$_ })
    if ($capabilities -notcontains "tools") {
        throw "Selected Ollama model does not explicitly advertise the required 'tools' capability."
    }
}

function Get-AutomaticContext {
    param(
        [Parameter(Mandatory = $true)]$Info,
        [Parameter(Mandatory = $true)][string]$ModelName,
        [Nullable[long]]$RequestedContext
    )

    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    } catch {
        throw "Windows physical-memory telemetry is unavailable; refusing to guess a Codex context. $($_.Exception.Message)"
    }
    if ($null -eq $os.FreePhysicalMemory -or [int64]$os.FreePhysicalMemory -le 0) {
        throw "Windows did not report available physical memory; refusing to guess a Codex context."
    }
    $availableMemoryBytes = [int64]$os.FreePhysicalMemory * 1KB

    try {
        $tags = Invoke-RestMethod -Uri "$baseUrl/api/tags" -Method Get -TimeoutSec 10
    } catch {
        throw "Could not inspect installed Ollama model sizes for context planning. $($_.Exception.Message)"
    }
    $tag = @($tags.models | Where-Object { $_.name -eq $ModelName }) | Select-Object -First 1
    $weightBytes = 0L
    if (-not $tag -or -not [int64]::TryParse([string]$tag.size, [ref]$weightBytes) -or $weightBytes -le 0) {
        throw "Ollama did not report a valid installed-model size for '$ModelName'."
    }

    $selectionInput = @{
        model_info = $Info.model_info
        architecture = [string]$Info.details.family
        model_weight_bytes = $weightBytes
        available_memory_bytes = $availableMemoryBytes
        requested_context_tokens = $RequestedContext
    } | ConvertTo-Json -Depth 30 -Compress
    $helperPath = Join-Path (Split-Path -Parent $PSScriptRoot) "app\ollama_context.py"
    if (-not (Test-Path -LiteralPath $helperPath -PathType Leaf)) {
        throw "The shared Ollama context-selection module is missing: $helperPath"
    }
    $selectionOutput = $selectionInput | & python $helperPath
    if ($LASTEXITCODE -ne 0) {
        throw "Fail-closed Ollama context selection failed; see the metadata/host diagnostic above."
    }
    try {
        $selection = ($selectionOutput -join "`n") | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "The Ollama context-selection module returned invalid JSON."
    }
    if ($null -eq $selection.selected_context_tokens -or [int64]$selection.selected_context_tokens -lt 512) {
        throw "The Ollama context-selection module did not return a valid selected context."
    }
    return $selection
}

function Get-AllocatedContext {
    param([Parameter(Mandatory = $true)][string]$ModelName)
    try {
        $runtime = Invoke-RestMethod -Uri "$baseUrl/api/ps" -Method Get -TimeoutSec 10
    } catch {
        throw "Could not read Ollama's loaded-model context from /api/ps. $($_.Exception.Message)"
    }

    $loaded = @($runtime.models | Where-Object {
        $_ -and $_.name -and ([string]$_.name).Equals($ModelName, [System.StringComparison]::OrdinalIgnoreCase)
    })
    if ($loaded.Count -ne 1) {
        throw "Ollama did not report exactly one loaded instance of '$ModelName' in /api/ps."
    }
    $allocated = 0L
    if ($null -eq $loaded[0].context_length -or -not [int64]::TryParse([string]$loaded[0].context_length, [ref]$allocated)) {
        throw "Ollama /api/ps did not report a valid allocated context length for '$ModelName'."
    }
    return $allocated
}

if ($VerifyOnly) {
    $codexModel = $Model
    $selectedContext = 0L
    if (-not [int64]::TryParse([string]$env:AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH, [ref]$selectedContext) -or $selectedContext -lt 512) {
        throw "The selected Codex context was not persisted for runtime verification."
    }
    $modelInfo = Get-ModelInfo -ModelName $codexModel
    Assert-ToolCallingCapability -Info $modelInfo
    $maximum = Get-MaximumContext -Info $modelInfo
    if ($selectedContext -gt $maximum) {
        throw "The persisted Codex context $selectedContext exceeds the model maximum $maximum."
    }
    $savedContext = [regex]::Match(
        [string]$modelInfo.modelfile,
        '(?im)^\s*PARAMETER\s+num_ctx\s+(\d+)\s*
} else {
    if ($Model -notmatch '^[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9._-]+)?$') {
        throw "Model name contains unsupported characters; refusing to construct a persistent Codex model variant."
    }

    $baseModel = $Model
    $modelInfo = Get-ModelInfo -ModelName $baseModel
    Assert-ToolCallingCapability -Info $modelInfo
    $maximum = Get-MaximumContext -Info $modelInfo

    $requestedContext = $null
    $manualContext = [string]$env:AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH
    if (-not [string]::IsNullOrWhiteSpace($manualContext)) {
        $parsedContext = 0L
        if (-not [int64]::TryParse($manualContext, [ref]$parsedContext) -or $parsedContext -lt 512) {
            throw "AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH must be an integer of at least 512."
        }
        $requestedContext = [Nullable[long]]$parsedContext
        $selectionMode = "requested-and-safely-clamped"
    } else {
        $selectionMode = "automatic"
    }
    $contextSelection = Get-AutomaticContext -Info $modelInfo -ModelName $baseModel -RequestedContext $requestedContext
    $selectedContext = [int64]$contextSelection.selected_context_tokens
    if ($selectedContext -gt $maximum) {
        throw "Selected context $selectedContext exceeds the model maximum $maximum."
    }
    Write-Host "Model maximum context: $($contextSelection.maximum_context_tokens) tokens"
    Write-Host "Hardware-safe context ceiling: $($contextSelection.hardware_maximum_context_tokens) tokens"
    if ($null -ne $requestedContext) {
        Write-Host "Requested context: $requestedContext tokens"
    }
    Write-Host "Final context: min(requested, model maximum, safe hardware capacity) = $selectedContext tokens"

    $codexModel = "$baseModel-codex-ctx$selectedContext"
    $existingInfo = $null
    try {
        $existingInfo = Get-ModelInfo -ModelName $codexModel
    } catch { }
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
            $null = Invoke-RestMethod -Uri "$baseUrl/api/create" -Method Post -ContentType "application/json" -Body $createBody -TimeoutSec $TimeoutSeconds
        } catch {
            throw "Ollama could not create persistent Codex model '$codexModel' at context $selectedContext. $($_.Exception.Message)"
        }
    }

    $modelInfo = Get-ModelInfo -ModelName $codexModel
    Assert-ToolCallingCapability -Info $modelInfo
    $savedContext = [regex]::Match(
        [string]$modelInfo.modelfile,
        '(?im)^\s*PARAMETER\s+num_ctx\s+(\d+)\s*$'
    )
    if (-not $savedContext.Success -or [int64]$savedContext.Groups[1].Value -ne $selectedContext) {
        throw "Persistent model '$codexModel' does not declare the selected num_ctx $selectedContext."
    }

    if ($env:GITHUB_ENV) {
        Add-Content -LiteralPath $env:GITHUB_ENV -Value "AUTONOMOUS_ENGINEERING_MODEL=$codexModel" -Encoding utf8
        Add-Content -LiteralPath $env:GITHUB_ENV -Value "AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH=$selectedContext" -Encoding utf8
    } else {
        $env:AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH = [string]$selectedContext
    }
    Write-Host "Context selection mode: $selectionMode"
}

$warmupBody = @{
    model = $codexModel
    prompt = "Reply with OK."
    stream = $false
    keep_alive = "10m"
} | ConvertTo-Json -Depth 4 -Compress
try {
    $null = Invoke-RestMethod -Uri "$baseUrl/api/generate" -Method Post -ContentType "application/json" -Body $warmupBody -TimeoutSec $TimeoutSeconds
} catch {
    throw "Ollama could not load persistent Codex model '$codexModel' at context $selectedContext. $($_.Exception.Message)"
}

$allocated = Get-AllocatedContext -ModelName $codexModel
Write-Host "Ollama base model: $Model"
Write-Host "Persistent Codex model: $codexModel"
Write-Host "Model maximum context: $maximum tokens"
Write-Host "Selected Codex context: $selectedContext tokens"
Write-Host "Ollama allocated runtime context: $allocated tokens"
if ($allocated -ne $selectedContext) {
    throw "Ollama allocated $allocated tokens for '$codexModel', but the verified selection is exactly $selectedContext. No Codex task may proceed."
}
Write-Host "OLLAMA_RUNTIME_CONTEXT_OK"

    )
    if (-not $savedContext.Success -or [int64]$savedContext.Groups[1].Value -ne $selectedContext) {
        throw "Persistent model '$codexModel' does not retain the selected num_ctx $selectedContext."
    }
} else {
    if ($Model -notmatch '^[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9._-]+)?$') {
        throw "Model name contains unsupported characters; refusing to construct a persistent Codex model variant."
    }

    $baseModel = $Model
    $modelInfo = Get-ModelInfo -ModelName $baseModel
    Assert-ToolCallingCapability -Info $modelInfo
    $maximum = Get-MaximumContext -Info $modelInfo

    $requestedContext = $null
    $manualContext = [string]$env:AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH
    if (-not [string]::IsNullOrWhiteSpace($manualContext)) {
        $parsedContext = 0L
        if (-not [int64]::TryParse($manualContext, [ref]$parsedContext) -or $parsedContext -lt 512) {
            throw "AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH must be an integer of at least 512."
        }
        $requestedContext = [Nullable[long]]$parsedContext
        $selectionMode = "requested-and-safely-clamped"
    } else {
        $selectionMode = "automatic"
    }
    $contextSelection = Get-AutomaticContext -Info $modelInfo -ModelName $baseModel -RequestedContext $requestedContext
    $selectedContext = [int64]$contextSelection.selected_context_tokens
    if ($selectedContext -gt $maximum) {
        throw "Selected context $selectedContext exceeds the model maximum $maximum."
    }
    Write-Host "Model maximum context: $($contextSelection.maximum_context_tokens) tokens"
    Write-Host "Hardware-safe context ceiling: $($contextSelection.hardware_maximum_context_tokens) tokens"
    if ($null -ne $requestedContext) {
        Write-Host "Requested context: $requestedContext tokens"
    }
    Write-Host "Final context: min(requested, model maximum, safe hardware capacity) = $selectedContext tokens"

    $codexModel = "$baseModel-codex-ctx$selectedContext"
    $existingInfo = $null
    try {
        $existingInfo = Get-ModelInfo -ModelName $codexModel
    } catch { }
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
            $null = Invoke-RestMethod -Uri "$baseUrl/api/create" -Method Post -ContentType "application/json" -Body $createBody -TimeoutSec $TimeoutSeconds
        } catch {
            throw "Ollama could not create persistent Codex model '$codexModel' at context $selectedContext. $($_.Exception.Message)"
        }
    }

    $modelInfo = Get-ModelInfo -ModelName $codexModel
    Assert-ToolCallingCapability -Info $modelInfo
    $savedContext = [regex]::Match(
        [string]$modelInfo.modelfile,
        '(?im)^\s*PARAMETER\s+num_ctx\s+(\d+)\s*$'
    )
    if (-not $savedContext.Success -or [int64]$savedContext.Groups[1].Value -ne $selectedContext) {
        throw "Persistent model '$codexModel' does not declare the selected num_ctx $selectedContext."
    }

    if ($env:GITHUB_ENV) {
        Add-Content -LiteralPath $env:GITHUB_ENV -Value "AUTONOMOUS_ENGINEERING_MODEL=$codexModel" -Encoding utf8
        Add-Content -LiteralPath $env:GITHUB_ENV -Value "AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH=$selectedContext" -Encoding utf8
    } else {
        $env:AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH = [string]$selectedContext
    }
    Write-Host "Context selection mode: $selectionMode"
}

$warmupBody = @{
    model = $codexModel
    prompt = "Reply with OK."
    stream = $false
    keep_alive = "10m"
} | ConvertTo-Json -Depth 4 -Compress
try {
    $null = Invoke-RestMethod -Uri "$baseUrl/api/generate" -Method Post -ContentType "application/json" -Body $warmupBody -TimeoutSec $TimeoutSeconds
} catch {
    throw "Ollama could not load persistent Codex model '$codexModel' at context $selectedContext. $($_.Exception.Message)"
}

$allocated = Get-AllocatedContext -ModelName $codexModel
Write-Host "Ollama base model: $Model"
Write-Host "Persistent Codex model: $codexModel"
Write-Host "Model maximum context: $maximum tokens"
Write-Host "Selected Codex context: $selectedContext tokens"
Write-Host "Ollama allocated runtime context: $allocated tokens"
if ($allocated -lt $selectedContext) {
    throw "Ollama allocated only $allocated tokens for '$codexModel'; selected context is $selectedContext. No Codex task may proceed."
}
Write-Host "OLLAMA_RUNTIME_CONTEXT_OK"
