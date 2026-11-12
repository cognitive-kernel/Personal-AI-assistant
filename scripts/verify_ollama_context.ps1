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
$minimumContextTokens = 1

function Get-ModelInfo {
    param([Parameter(Mandatory = $true)][string]$ModelName)
    $body = @{ model = $ModelName; verbose = $false } | ConvertTo-Json -Compress
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
    if (-not $property -or -not [int64]::TryParse([string]$property.Value, [ref]$maximum) -or $maximum -lt $minimumContextTokens) {
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
        $tags = Invoke-RestMethod -Uri "$baseUrl/api/tags" -Method Get -TimeoutSec 10 -MaximumRedirection 0
    } catch {
        throw "Could not inspect installed Ollama model sizes for context planning. $($_.Exception.Message)"
    }
    $tag = @($tags.models | Where-Object { $_.name -eq $ModelName }) | Select-Object -First 1
    $weightBytes = 0L
    if (-not $tag -or -not [int64]::TryParse([string]$tag.size, [ref]$weightBytes) -or $weightBytes -le 0) {
        throw "Ollama did not report a valid installed-model size for '$ModelName'."
    }

    try {
        $runtime = Invoke-RestMethod -Uri "$baseUrl/api/ps" -Method Get -TimeoutSec 10 -MaximumRedirection 0
    } catch {
        throw "Could not determine whether the selected Ollama model is already resident in memory. $($_.Exception.Message)"
    }
    $loadedBase = @($runtime.models | Where-Object {
        $_ -and $_.name -and ([string]$_.name).Equals($ModelName, [System.StringComparison]::OrdinalIgnoreCase)
    })
    if ($loadedBase.Count -gt 1) {
        throw "Ollama reported multiple loaded instances of selected model '$ModelName'; refusing ambiguous memory accounting."
    }
    $modelWeightsAlreadyLoaded = $loadedBase.Count -eq 1

    $selectionInput = @{
        model_info = $Info.model_info
        architecture = [string]$Info.details.family
        model_weight_bytes = $weightBytes
        model_weights_already_loaded = $modelWeightsAlreadyLoaded
        available_memory_bytes = $availableMemoryBytes
        requested_context_tokens = $RequestedContext
    } | ConvertTo-Json -Depth 30 -Compress
    $helperPath = Join-Path (Split-Path -Parent $PSScriptRoot) "app\ollama_context.py"
    if (-not (Test-Path -LiteralPath $helperPath -PathType Leaf)) {
        throw "The shared Ollama context-selection module is missing: $helperPath"
    }
    $selectionPath = Join-Path $env:TEMP ("ollama-context-selection-" + [guid]::NewGuid().ToString("N") + ".json")
    $selectionOutputPath = "$selectionPath.stdout"
    $selectionErrorPath = "$selectionPath.stderr"
    try {
        [System.IO.File]::WriteAllText($selectionPath, $selectionInput, [System.Text.UTF8Encoding]::new($false))
        $pythonPath = (Get-Command python.exe -ErrorAction Stop).Source
        $argumentList = '"' + $helperPath + '" --input-file "' + $selectionPath + '"'
        $selectionProcess = Start-Process -FilePath $pythonPath -ArgumentList $argumentList -NoNewWindow -PassThru -Wait -RedirectStandardOutput $selectionOutputPath -RedirectStandardError $selectionErrorPath
        if ($selectionProcess.ExitCode -ne 0) {
            $selectionError = if (Test-Path -LiteralPath $selectionErrorPath) {
                [System.IO.File]::ReadAllText($selectionErrorPath, [System.Text.Encoding]::UTF8)
            } else { "" }
            throw "Fail-closed Ollama context selection failed: $selectionError"
        }
        try {
            $selectionJson = [System.IO.File]::ReadAllText($selectionOutputPath, [System.Text.Encoding]::UTF8)
            $selection = $selectionJson | ConvertFrom-Json -ErrorAction Stop
        } catch {
            throw "The Ollama context-selection module returned invalid JSON. $($_.Exception.Message)"
        }
    } finally {
        foreach ($temporaryPath in @($selectionPath, $selectionOutputPath, $selectionErrorPath)) {
            if (Test-Path -LiteralPath $temporaryPath) {
                Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
            }
        }
    }
    if ($null -eq $selection.selected_context_tokens -or [int64]$selection.selected_context_tokens -lt $minimumContextTokens) {
        throw "The Ollama context-selection module did not return a positive runtime context."
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

function Stop-LoadedModel {
    param([Parameter(Mandatory = $true)][string]$ModelName)

    try {
        $runtime = Invoke-RestMethod -Uri "$baseUrl/api/ps" -Method Get -TimeoutSec 10 -MaximumRedirection 0
    } catch {
        throw "Could not inspect loaded Ollama models before releasing '$ModelName'. $($_.Exception.Message)"
    }
    $loaded = @($runtime.models | Where-Object {
        $_ -and $_.name -and ([string]$_.name).Equals($ModelName, [System.StringComparison]::OrdinalIgnoreCase)
    })
    if ($loaded.Count -eq 0) {
        return
    }
    if ($loaded.Count -ne 1) {
        throw "Ollama reported multiple loaded instances of '$ModelName'; refusing ambiguous cleanup."
    }

    $unloadBody = @{
        model = $ModelName
        prompt = ""
        stream = $false
        keep_alive = 0
    } | ConvertTo-Json -Compress
    try {
        $null = Invoke-RestMethod -Uri "$baseUrl/api/generate" -Method Post -Body $unloadBody -ContentType "application/json" -TimeoutSec 30 -MaximumRedirection 0
    } catch {
        throw "Ollama could not unload internal Codex model '$ModelName'. $($_.Exception.Message)"
    }

    try {
        $after = Invoke-RestMethod -Uri "$baseUrl/api/ps" -Method Get -TimeoutSec 10 -MaximumRedirection 0
    } catch {
        throw "Could not verify that internal Codex model '$ModelName' was unloaded. $($_.Exception.Message)"
    }
    $remaining = @($after.models | Where-Object {
        $_ -and $_.name -and ([string]$_.name).Equals($ModelName, [System.StringComparison]::OrdinalIgnoreCase)
    })
    if ($remaining.Count -ne 0) {
        throw "Internal Codex model '$ModelName' remains loaded after the unload request."
    }
    Write-Host "Released internal Codex model from RAM: $ModelName"
}

function Stop-LoadedCodexVariants {
    param([Parameter(Mandatory = $true)][string]$BaseModel)

    try {
        $runtime = Invoke-RestMethod -Uri "$baseUrl/api/ps" -Method Get -TimeoutSec 10 -MaximumRedirection 0
    } catch {
        throw "Could not inspect loaded Ollama models before context selection. $($_.Exception.Message)"
    }
    $prefix = "$BaseModel-codex-ctx"
    $legacyPattern = '^' + [regex]::Escape("$BaseModel-codex-") + '[0-9]+k
    foreach ($name in $names) {
        Stop-LoadedModel -ModelName $name
    }
}

if ($VerifyOnly) {
    $codexModel = $Model
    $selectedContext = 0L
    if (-not [int64]::TryParse([string]$env:AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH, [ref]$selectedContext) -or $selectedContext -lt $minimumContextTokens) {
        throw "The selected Codex context was not persisted at the positive runtime-context boundary."
    }
    $modelInfo = Get-ModelInfo -ModelName $codexModel
    Assert-ToolCallingCapability -Info $modelInfo
    $maximum = Get-MaximumContext -Info $modelInfo
    if ($maximum -lt $minimumContextTokens) {
        throw "Selected model maximum context $maximum is below the positive runtime-context boundary."
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
    if ($Model -match '(?i)-codex-(?:ctx[0-9]+|[0-9]+k)

    $baseModel = $Model
    Stop-LoadedCodexVariants -BaseModel $baseModel
    $modelInfo = Get-ModelInfo -ModelName $baseModel
    Assert-ToolCallingCapability -Info $modelInfo
    $maximum = Get-MaximumContext -Info $modelInfo
    if ($maximum -lt $minimumContextTokens) {
        throw "Selected model maximum context $maximum is below the positive runtime-context boundary."
    }

    $requestedContext = $null
    $manualContext = [string]$env:AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH
    if (-not [string]::IsNullOrWhiteSpace($manualContext)) {
        $parsedContext = 0L
        if (-not [int64]::TryParse($manualContext, [ref]$parsedContext) -or $parsedContext -lt $minimumContextTokens) {
            throw "AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH must be a positive integer."
        }
        $requestedContext = [Nullable[long]]$parsedContext
        $selectionMode = "requested-and-safely-clamped"
    } else {
        $selectionMode = "automatic"
    }

    $contextSelection = Get-AutomaticContext -Info $modelInfo -ModelName $baseModel -RequestedContext $requestedContext
    $selectedContext = [int64]$contextSelection.selected_context_tokens
    if ($selectedContext -lt $minimumContextTokens -or $selectedContext -gt $maximum) {
        throw "Selected context $selectedContext is outside the model's supported context range $maximum."
    }

    Write-Host "Model maximum context: $($contextSelection.maximum_context_tokens) tokens"
    Write-Host "Hardware-safe context ceiling: $($contextSelection.hardware_maximum_context_tokens) tokens"
    Write-Host "Available physical RAM: $($contextSelection.available_memory_bytes) bytes"
    Write-Host "Installed model weight size: $($contextSelection.model_weight_bytes) bytes"
    Write-Host "Model weights already resident: $([bool]$contextSelection.model_weights_already_loaded)"
    Write-Host "Model weight charge in this selection: $($contextSelection.model_weight_charge_bytes) bytes"
    Write-Host "Estimated KV cache: $($contextSelection.kv_cache_bytes_per_token) bytes/token"
    Write-Host "Reserved system RAM: $($contextSelection.reserve_bytes) bytes"
    Write-Host "Model/runtime overhead budget: $($contextSelection.overhead_bytes) bytes"
    if ($null -ne $requestedContext) {
        Write-Host "Requested context: $requestedContext tokens"
    }
    Write-Host "Final context: min(requested, model maximum, safe hardware capacity, Codex catalog maximum) = $selectedContext tokens"

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
Write-Host "Ollama model passed to Codex: $codexModel"
Write-Host "Persistent Codex model: $codexModel"
Write-Host "Model maximum context: $maximum tokens"
Write-Host "Selected Codex context: $selectedContext tokens"
Write-Host "Ollama allocated runtime context: $allocated tokens"
if ($allocated -ne $selectedContext) {
    Stop-LoadedModel -ModelName $codexModel
    throw "Ollama allocated $allocated tokens for '$codexModel', but the verified selection is exactly $selectedContext. No Codex task may proceed."
}
if (-not $VerifyOnly) {
    try {
        $osAfterLoad = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        $remainingPhysicalMemoryBytes = [int64]$osAfterLoad.FreePhysicalMemory * 1KB
    } catch {
        Stop-LoadedModel -ModelName $codexModel
        throw "Could not verify remaining physical RAM after Ollama context allocation. $($_.Exception.Message)"
    }
    if ($remainingPhysicalMemoryBytes -lt [int64]$contextSelection.reserve_bytes) {
        Stop-LoadedModel -ModelName $codexModel
        throw "Ollama context allocation left $remainingPhysicalMemoryBytes bytes of free physical RAM, below the required reserve of $($contextSelection.reserve_bytes). No Codex task may proceed."
    }
    Write-Host "Remaining physical RAM after Ollama allocation: $remainingPhysicalMemoryBytes bytes"
}
Write-Host "OLLAMA_RUNTIME_CONTEXT_OK"

if ($VerifyOnly) {
    Stop-LoadedModel -ModelName $codexModel
    Write-Host "OLLAMA_RUNTIME_CONTEXT_RELEASED"
}
) {
        throw "The selected model is an internal Codex context variant (current or legacy). Select the original Ollama model to prevent nested variants."
    }

    $baseModel = $Model
    Stop-LoadedCodexVariants -BaseModel $baseModel
    $modelInfo = Get-ModelInfo -ModelName $baseModel
    Assert-ToolCallingCapability -Info $modelInfo
    $maximum = Get-MaximumContext -Info $modelInfo
    if ($maximum -lt $minimumContextTokens) {
        throw "Selected model maximum context $maximum is below the positive runtime-context boundary."
    }

    $requestedContext = $null
    $manualContext = [string]$env:AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH
    if (-not [string]::IsNullOrWhiteSpace($manualContext)) {
        $parsedContext = 0L
        if (-not [int64]::TryParse($manualContext, [ref]$parsedContext) -or $parsedContext -lt $minimumContextTokens) {
            throw "AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH must be a positive integer."
        }
        $requestedContext = [Nullable[long]]$parsedContext
        $selectionMode = "requested-and-safely-clamped"
    } else {
        $selectionMode = "automatic"
    }

    $contextSelection = Get-AutomaticContext -Info $modelInfo -ModelName $baseModel -RequestedContext $requestedContext
    $selectedContext = [int64]$contextSelection.selected_context_tokens
    if ($selectedContext -lt $minimumContextTokens -or $selectedContext -gt $maximum) {
        throw "Selected context $selectedContext is outside the model's supported context range $maximum."
    }

    Write-Host "Model maximum context: $($contextSelection.maximum_context_tokens) tokens"
    Write-Host "Hardware-safe context ceiling: $($contextSelection.hardware_maximum_context_tokens) tokens"
    Write-Host "Available physical RAM: $($contextSelection.available_memory_bytes) bytes"
    Write-Host "Installed model weight size: $($contextSelection.model_weight_bytes) bytes"
    Write-Host "Model weights already resident: $([bool]$contextSelection.model_weights_already_loaded)"
    Write-Host "Model weight charge in this selection: $($contextSelection.model_weight_charge_bytes) bytes"
    Write-Host "Estimated KV cache: $($contextSelection.kv_cache_bytes_per_token) bytes/token"
    Write-Host "Reserved system RAM: $($contextSelection.reserve_bytes) bytes"
    Write-Host "Model/runtime overhead budget: $($contextSelection.overhead_bytes) bytes"
    if ($null -ne $requestedContext) {
        Write-Host "Requested context: $requestedContext tokens"
    }
    Write-Host "Final context: min(requested, model maximum, safe hardware capacity, Codex catalog maximum) = $selectedContext tokens"

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
Write-Host "Ollama model passed to Codex: $codexModel"
Write-Host "Persistent Codex model: $codexModel"
Write-Host "Model maximum context: $maximum tokens"
Write-Host "Selected Codex context: $selectedContext tokens"
Write-Host "Ollama allocated runtime context: $allocated tokens"
if ($allocated -ne $selectedContext) {
    Stop-LoadedModel -ModelName $codexModel
    throw "Ollama allocated $allocated tokens for '$codexModel', but the verified selection is exactly $selectedContext. No Codex task may proceed."
}
if (-not $VerifyOnly) {
    try {
        $osAfterLoad = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        $remainingPhysicalMemoryBytes = [int64]$osAfterLoad.FreePhysicalMemory * 1KB
    } catch {
        Stop-LoadedModel -ModelName $codexModel
        throw "Could not verify remaining physical RAM after Ollama context allocation. $($_.Exception.Message)"
    }
    if ($remainingPhysicalMemoryBytes -lt [int64]$contextSelection.reserve_bytes) {
        Stop-LoadedModel -ModelName $codexModel
        throw "Ollama context allocation left $remainingPhysicalMemoryBytes bytes of free physical RAM, below the required reserve of $($contextSelection.reserve_bytes). No Codex task may proceed."
    }
    Write-Host "Remaining physical RAM after Ollama allocation: $remainingPhysicalMemoryBytes bytes"
}
Write-Host "OLLAMA_RUNTIME_CONTEXT_OK"

if ($VerifyOnly) {
    Stop-LoadedModel -ModelName $codexModel
    Write-Host "OLLAMA_RUNTIME_CONTEXT_RELEASED"
}

    $names = @(
        $runtime.models |
            Where-Object {
                if (-not $_ -or -not $_.name) { return $false }
                $candidate = [string]$_.name
                $isCurrentVariant = $false
                if ($candidate.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                    $suffix = $candidate.Substring($prefix.Length)
                    $isCurrentVariant = ($suffix.Length -gt 0) -and ($suffix -notmatch '[^0-9]')
                }
                $isLegacyVariant = [regex]::IsMatch(
                    $candidate,
                    $legacyPattern,
                    [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
                )
                $isCurrentVariant -or $isLegacyVariant
            } |
            ForEach-Object { [string]$_.name }
    )
    foreach ($name in $names) {
        Stop-LoadedModel -ModelName $name
    }
}

if ($VerifyOnly) {
    $codexModel = $Model
    $selectedContext = 0L
    if (-not [int64]::TryParse([string]$env:AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH, [ref]$selectedContext) -or $selectedContext -lt $minimumContextTokens) {
        throw "The selected Codex context was not persisted at the positive runtime-context boundary."
    }
    $modelInfo = Get-ModelInfo -ModelName $codexModel
    Assert-ToolCallingCapability -Info $modelInfo
    $maximum = Get-MaximumContext -Info $modelInfo
    if ($maximum -lt $minimumContextTokens) {
        throw "Selected model maximum context $maximum is below the positive runtime-context boundary."
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
    if ($Model -match '(?i)-codex-(?:ctx[0-9]+|[0-9]+k)

    $baseModel = $Model
    Stop-LoadedCodexVariants -BaseModel $baseModel
    $modelInfo = Get-ModelInfo -ModelName $baseModel
    Assert-ToolCallingCapability -Info $modelInfo
    $maximum = Get-MaximumContext -Info $modelInfo
    if ($maximum -lt $minimumContextTokens) {
        throw "Selected model maximum context $maximum is below the positive runtime-context boundary."
    }

    $requestedContext = $null
    $manualContext = [string]$env:AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH
    if (-not [string]::IsNullOrWhiteSpace($manualContext)) {
        $parsedContext = 0L
        if (-not [int64]::TryParse($manualContext, [ref]$parsedContext) -or $parsedContext -lt $minimumContextTokens) {
            throw "AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH must be a positive integer."
        }
        $requestedContext = [Nullable[long]]$parsedContext
        $selectionMode = "requested-and-safely-clamped"
    } else {
        $selectionMode = "automatic"
    }

    $contextSelection = Get-AutomaticContext -Info $modelInfo -ModelName $baseModel -RequestedContext $requestedContext
    $selectedContext = [int64]$contextSelection.selected_context_tokens
    if ($selectedContext -lt $minimumContextTokens -or $selectedContext -gt $maximum) {
        throw "Selected context $selectedContext is outside the model's supported context range $maximum."
    }

    Write-Host "Model maximum context: $($contextSelection.maximum_context_tokens) tokens"
    Write-Host "Hardware-safe context ceiling: $($contextSelection.hardware_maximum_context_tokens) tokens"
    Write-Host "Available physical RAM: $($contextSelection.available_memory_bytes) bytes"
    Write-Host "Installed model weight size: $($contextSelection.model_weight_bytes) bytes"
    Write-Host "Model weights already resident: $([bool]$contextSelection.model_weights_already_loaded)"
    Write-Host "Model weight charge in this selection: $($contextSelection.model_weight_charge_bytes) bytes"
    Write-Host "Estimated KV cache: $($contextSelection.kv_cache_bytes_per_token) bytes/token"
    Write-Host "Reserved system RAM: $($contextSelection.reserve_bytes) bytes"
    Write-Host "Model/runtime overhead budget: $($contextSelection.overhead_bytes) bytes"
    if ($null -ne $requestedContext) {
        Write-Host "Requested context: $requestedContext tokens"
    }
    Write-Host "Final context: min(requested, model maximum, safe hardware capacity, Codex catalog maximum) = $selectedContext tokens"

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
Write-Host "Ollama model passed to Codex: $codexModel"
Write-Host "Persistent Codex model: $codexModel"
Write-Host "Model maximum context: $maximum tokens"
Write-Host "Selected Codex context: $selectedContext tokens"
Write-Host "Ollama allocated runtime context: $allocated tokens"
if ($allocated -ne $selectedContext) {
    Stop-LoadedModel -ModelName $codexModel
    throw "Ollama allocated $allocated tokens for '$codexModel', but the verified selection is exactly $selectedContext. No Codex task may proceed."
}
if (-not $VerifyOnly) {
    try {
        $osAfterLoad = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        $remainingPhysicalMemoryBytes = [int64]$osAfterLoad.FreePhysicalMemory * 1KB
    } catch {
        Stop-LoadedModel -ModelName $codexModel
        throw "Could not verify remaining physical RAM after Ollama context allocation. $($_.Exception.Message)"
    }
    if ($remainingPhysicalMemoryBytes -lt [int64]$contextSelection.reserve_bytes) {
        Stop-LoadedModel -ModelName $codexModel
        throw "Ollama context allocation left $remainingPhysicalMemoryBytes bytes of free physical RAM, below the required reserve of $($contextSelection.reserve_bytes). No Codex task may proceed."
    }
    Write-Host "Remaining physical RAM after Ollama allocation: $remainingPhysicalMemoryBytes bytes"
}
Write-Host "OLLAMA_RUNTIME_CONTEXT_OK"

if ($VerifyOnly) {
    Stop-LoadedModel -ModelName $codexModel
    Write-Host "OLLAMA_RUNTIME_CONTEXT_RELEASED"
}
) {
        throw "The selected model is an internal Codex context variant (current or legacy). Select the original Ollama model to prevent nested variants."
    }

    $baseModel = $Model
    Stop-LoadedCodexVariants -BaseModel $baseModel
    $modelInfo = Get-ModelInfo -ModelName $baseModel
    Assert-ToolCallingCapability -Info $modelInfo
    $maximum = Get-MaximumContext -Info $modelInfo
    if ($maximum -lt $minimumContextTokens) {
        throw "Selected model maximum context $maximum is below the positive runtime-context boundary."
    }

    $requestedContext = $null
    $manualContext = [string]$env:AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH
    if (-not [string]::IsNullOrWhiteSpace($manualContext)) {
        $parsedContext = 0L
        if (-not [int64]::TryParse($manualContext, [ref]$parsedContext) -or $parsedContext -lt $minimumContextTokens) {
            throw "AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH must be a positive integer."
        }
        $requestedContext = [Nullable[long]]$parsedContext
        $selectionMode = "requested-and-safely-clamped"
    } else {
        $selectionMode = "automatic"
    }

    $contextSelection = Get-AutomaticContext -Info $modelInfo -ModelName $baseModel -RequestedContext $requestedContext
    $selectedContext = [int64]$contextSelection.selected_context_tokens
    if ($selectedContext -lt $minimumContextTokens -or $selectedContext -gt $maximum) {
        throw "Selected context $selectedContext is outside the model's supported context range $maximum."
    }

    Write-Host "Model maximum context: $($contextSelection.maximum_context_tokens) tokens"
    Write-Host "Hardware-safe context ceiling: $($contextSelection.hardware_maximum_context_tokens) tokens"
    Write-Host "Available physical RAM: $($contextSelection.available_memory_bytes) bytes"
    Write-Host "Installed model weight size: $($contextSelection.model_weight_bytes) bytes"
    Write-Host "Model weights already resident: $([bool]$contextSelection.model_weights_already_loaded)"
    Write-Host "Model weight charge in this selection: $($contextSelection.model_weight_charge_bytes) bytes"
    Write-Host "Estimated KV cache: $($contextSelection.kv_cache_bytes_per_token) bytes/token"
    Write-Host "Reserved system RAM: $($contextSelection.reserve_bytes) bytes"
    Write-Host "Model/runtime overhead budget: $($contextSelection.overhead_bytes) bytes"
    if ($null -ne $requestedContext) {
        Write-Host "Requested context: $requestedContext tokens"
    }
    Write-Host "Final context: min(requested, model maximum, safe hardware capacity, Codex catalog maximum) = $selectedContext tokens"

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
Write-Host "Ollama model passed to Codex: $codexModel"
Write-Host "Persistent Codex model: $codexModel"
Write-Host "Model maximum context: $maximum tokens"
Write-Host "Selected Codex context: $selectedContext tokens"
Write-Host "Ollama allocated runtime context: $allocated tokens"
if ($allocated -ne $selectedContext) {
    Stop-LoadedModel -ModelName $codexModel
    throw "Ollama allocated $allocated tokens for '$codexModel', but the verified selection is exactly $selectedContext. No Codex task may proceed."
}
if (-not $VerifyOnly) {
    try {
        $osAfterLoad = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        $remainingPhysicalMemoryBytes = [int64]$osAfterLoad.FreePhysicalMemory * 1KB
    } catch {
        Stop-LoadedModel -ModelName $codexModel
        throw "Could not verify remaining physical RAM after Ollama context allocation. $($_.Exception.Message)"
    }
    if ($remainingPhysicalMemoryBytes -lt [int64]$contextSelection.reserve_bytes) {
        Stop-LoadedModel -ModelName $codexModel
        throw "Ollama context allocation left $remainingPhysicalMemoryBytes bytes of free physical RAM, below the required reserve of $($contextSelection.reserve_bytes). No Codex task may proceed."
    }
    Write-Host "Remaining physical RAM after Ollama allocation: $remainingPhysicalMemoryBytes bytes"
}
Write-Host "OLLAMA_RUNTIME_CONTEXT_OK"

if ($VerifyOnly) {
    Stop-LoadedModel -ModelName $codexModel
    Write-Host "OLLAMA_RUNTIME_CONTEXT_RELEASED"
}
