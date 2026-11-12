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
        Invoke-RestMethod -Uri "$baseUrl/api/show" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 20 -MaximumRedirection 0
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
        [Nullable[long]]$RequestedContext,
        [bool]$ModelWeightsAlreadyLoaded = $false
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
        $tags = Invoke-RestMethod -Uri "$baseUrl/api/tags" -Method Get -TimeoutSec 10 -MaximumRedirection 0
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
        model_weights_already_loaded = $ModelWeightsAlreadyLoaded
        available_memory_bytes = $availableMemoryBytes
        requested_context_tokens = $RequestedContext
    } | ConvertTo-Json -Depth 30 -Compress
    $helperPath = Join-Path (Split-Path -Parent $PSScriptRoot) "app\ollama_context.py"
    if (-not (Test-Path -LiteralPath $helperPath -PathType Leaf)) {
        throw "The shared Ollama context-selection module is missing: $helperPath"
    }
    $inputPath = Join-Path ([IO.Path]::GetTempPath()) ("ollama-context-selection-" + [guid]::NewGuid().ToString("N") + ".json")
    try {
        # Windows PowerShell 5.1 pipes native-process stdin as UTF-16LE. Use an
        # explicit UTF-8 file contract so Python receives identical JSON on all shells.
        $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
        [IO.File]::WriteAllText($inputPath, $selectionInput, $utf8NoBom)
        $selectionOutput = & python $helperPath --input-file $inputPath
        $helperExitCode = $LASTEXITCODE
        if ($helperExitCode -ne 0) {
            throw "Fail-closed Ollama context selection failed with exit code $helperExitCode."
        }
        try {
            $selection = ($selectionOutput -join [Environment]::NewLine) | ConvertFrom-Json -ErrorAction Stop
        } catch {
            throw "The Ollama context-selection module returned invalid JSON."
        }
    } finally {
        if (Test-Path -LiteralPath $inputPath) {
            Remove-Item -LiteralPath $inputPath -Force -ErrorAction SilentlyContinue
        }
    }
    if ($null -eq $selection.selected_context_tokens -or [int64]$selection.selected_context_tokens -lt 64000) {
        throw "The Ollama context-selection module did not return the required minimum 64000-token Codex context."
    }
    return $selection
}

function Get-AllocatedContext {
    param([Parameter(Mandatory = $true)][string]$ModelName)
    try {
        $runtime = Invoke-RestMethod -Uri "$baseUrl/api/ps" -Method Get -TimeoutSec 10 -MaximumRedirection 0
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

function Get-LoadedCodexContextVariant {
    param(
        [Parameter(Mandatory = $true)][string]$BaseModel,
        [Parameter(Mandatory = $true)][long]$MaximumContext,
        [Nullable[long]]$RequestedContext
    )

    try {
        $runtime = Invoke-RestMethod -Uri "$baseUrl/api/ps" -Method Get -TimeoutSec 10 -MaximumRedirection 0
    } catch {
        throw "Could not inspect existing Ollama allocations before context planning. $($_.Exception.Message)"
    }

    $pattern = '^' + [regex]::Escape("$BaseModel-codex-ctx") + '(?<context>[0-9]+)$'
    $verified = @()
    foreach ($entry in @($runtime.models)) {
        if ($null -eq $entry -or [string]::IsNullOrWhiteSpace([string]$entry.name)) {
            continue
        }
        $name = [string]$entry.name
        $match = [regex]::Match($name, $pattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if (-not $match.Success) {
            continue
        }

        $suffixContext = 0L
        $allocatedContext = 0L
        if (-not [int64]::TryParse($match.Groups["context"].Value, [ref]$suffixContext) -or
            $suffixContext -lt 64000 -or $suffixContext -gt $MaximumContext -or
            $null -eq $entry.context_length -or
            -not [int64]::TryParse([string]$entry.context_length, [ref]$allocatedContext) -or
            $allocatedContext -ne $suffixContext) {
            continue
        }
        if ($null -ne $RequestedContext -and $suffixContext -ne [long]$RequestedContext) {
            continue
        }

        try {
            $info = Get-ModelInfo -ModelName $name
        } catch {
            continue
        }
        $saved = [regex]::Match(
            [string]$info.modelfile,
            '(?im)^\s*PARAMETER\s+num_ctx\s+(\d+)\s*$'
        )
        if ($saved.Success -and [int64]$saved.Groups[1].Value -eq $suffixContext) {
            $verified += [PSCustomObject]@{ ModelName = $name; Context = $suffixContext }
        }
    }

    if ($verified.Count -eq 0) {
        return $null
    }
    return $verified | Sort-Object -Property Context -Descending | Select-Object -First 1
}

function Test-OllamaModelWeightsLoaded {
    param([Parameter(Mandatory = $true)][string]$BaseModel)

    try {
        $runtime = Invoke-RestMethod -Uri "$baseUrl/api/ps" -Method Get -TimeoutSec 10 -MaximumRedirection 0
    } catch {
        throw "Could not inspect Ollama's loaded models before RAM budgeting. $($_.Exception.Message)"
    }
    $variantPrefix = "$BaseModel-codex-ctx"
    $loaded = @($runtime.models | Where-Object {
        if ($null -eq $_ -or [string]::IsNullOrWhiteSpace([string]$_.name)) {
            return $false
        }
        $name = [string]$_.name
        $name.Equals($BaseModel, [System.StringComparison]::OrdinalIgnoreCase) -or
            $name.StartsWith($variantPrefix, [System.StringComparison]::OrdinalIgnoreCase)
    })
    return $loaded.Count -gt 0
}

if ($VerifyOnly) {
    $codexModel = $Model
    $selectedContext = 0L
    if (-not [int64]::TryParse([string]$env:AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH, [ref]$selectedContext) -or $selectedContext -lt 64000) {
        throw "The selected Codex context was not persisted at the required minimum of 64000 tokens for runtime verification."
    }
    $modelInfo = Get-ModelInfo -ModelName $codexModel
    Assert-ToolCallingCapability -Info $modelInfo
    $maximum = Get-MaximumContext -Info $modelInfo
    if ($maximum -lt 64000) {
        throw "Selected model maximum context $maximum is below the Codex minimum of 64000 tokens."
    }
    if ($selectedContext -gt $maximum) {
        throw "The persisted Codex context $selectedContext exceeds the model maximum $maximum."
    }
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
    $modelInfo = Get-ModelInfo -ModelName $baseModel
    Assert-ToolCallingCapability -Info $modelInfo
    $maximum = Get-MaximumContext -Info $modelInfo
    if ($maximum -lt 64000) {
        throw "Selected model maximum context $maximum is below the Codex minimum of 64000 tokens."
    }

    $requestedContext = $null
    $manualContext = [string]$env:AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH
    if (-not [string]::IsNullOrWhiteSpace($manualContext)) {
        $parsedContext = 0L
        if (-not [int64]::TryParse($manualContext, [ref]$parsedContext) -or $parsedContext -lt 64000) {
            throw "AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH must be an integer of at least 64000 for Codex."
        }
        $requestedContext = [Nullable[long]]$parsedContext
        $selectionMode = "requested-and-safely-clamped"
    } else {
        $selectionMode = "automatic"
    }

    $loadedVariant = Get-LoadedCodexContextVariant -BaseModel $baseModel -MaximumContext $maximum -RequestedContext $requestedContext
    if ($loadedVariant) {
        $codexModel = [string]$loadedVariant.ModelName
        $selectedContext = [int64]$loadedVariant.Context
        $selectionMode = "reused-verified-loaded-variant"
        Write-Host "Reusing already-loaded, verified Codex model variant: $codexModel"
        Write-Host "Verified allocated context: $selectedContext tokens"
    } else {
        $modelWeightsAlreadyLoaded = Test-OllamaModelWeightsLoaded -BaseModel $baseModel
        $contextSelection = Get-AutomaticContext -Info $modelInfo -ModelName $baseModel -RequestedContext $requestedContext -ModelWeightsAlreadyLoaded $modelWeightsAlreadyLoaded
        $selectedContext = [int64]$contextSelection.selected_context_tokens
    if ($selectedContext -lt 64000 -or $selectedContext -gt $maximum) {
        throw "Selected context $selectedContext is outside the supported Codex range for model maximum $maximum."
    }

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
    }
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
            $null = Invoke-RestMethod -Uri "$baseUrl/api/create" -Method Post -ContentType "application/json" -Body $createBody -TimeoutSec $TimeoutSeconds -MaximumRedirection 0
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
        $env:AUTONOMOUS_ENGINEERING_MODEL = $codexModel
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
    $null = Invoke-RestMethod -Uri "$baseUrl/api/generate" -Method Post -ContentType "application/json" -Body $warmupBody -TimeoutSec $TimeoutSeconds -MaximumRedirection 0
} catch {
    throw "Ollama could not load persistent Codex model '$codexModel' at context $selectedContext. $($_.Exception.Message)"
}

$allocated = Get-AllocatedContext -ModelName $codexModel
Write-Host "Ollama model passed to Codex: $Model"
Write-Host "Persistent Codex model: $codexModel"
Write-Host "Model maximum context: $maximum tokens"
Write-Host "Selected Codex context: $selectedContext tokens"
Write-Host "Ollama allocated runtime context: $allocated tokens"
if ($allocated -ne $selectedContext) {
    throw "Ollama allocated $allocated tokens for '$codexModel', but the verified selection is exactly $selectedContext. No Codex task may proceed."
}
Write-Host "OLLAMA_RUNTIME_CONTEXT_OK"
