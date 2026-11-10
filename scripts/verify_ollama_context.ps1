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
    $preferredKey = $architecture + ".context_length"
    $preferred = $Info.model_info.PSObject.Properties[$preferredKey]
    if ($preferred -and $preferred.Value -is [ValueType] -and [int64]$preferred.Value -ge 512) {
        return [int64]$preferred.Value
    }
    $values = @(
        $Info.model_info.PSObject.Properties |
        Where-Object {
            $_.Name -match '\.context_length$' -and
            $_.Value -is [ValueType] -and
            [int64]$_.Value -ge 512
        } |
        ForEach-Object { [int64]$_.Value }
    )
    if ($values.Count -eq 0) { return $null }
    [int64](($values | Measure-Object -Maximum).Maximum)
}

function Get-AutomaticContext {
    param(
        [Parameter(Mandatory = $true)][int64]$Maximum,
        [Parameter(Mandatory = $true)]$Info
    )

    $freeRamGiB = $null
    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        if ($os.FreePhysicalMemory -gt 0) {
            $freeRamGiB = [double]$os.FreePhysicalMemory / 1MB
        }
    } catch { }
    if ($null -eq $freeRamGiB) { return [int64][Math]::Min($Maximum, 2048) }

    $tags = Invoke-RestMethod -Uri "$baseUrl/api/tags" -Method Get -TimeoutSec 10
    $tag = @($tags.models | Where-Object { $_.name -eq $Model }) | Select-Object -First 1
    $weightsGiB = if ($tag -and $tag.size -gt 0) { [double]$tag.size / 1GB } else { 0 }
    $architecture = [string]$Info.details.family
    $modelInfo = $Info.model_info
    $layers = $null
    $heads = $null
    $keyLength = $null
    $valueLength = $null

    foreach ($property in $modelInfo.PSObject.Properties) {
        if ($property.Name -eq ($architecture + ".block_count")) { $layers = [double]$property.Value }
        if ($property.Name -eq ($architecture + ".attention.head_count_kv")) { $heads = [double]$property.Value }
        if ($property.Name -eq ($architecture + ".attention.key_length")) { $keyLength = [double]$property.Value }
        if ($property.Name -eq ($architecture + ".attention.value_length")) { $valueLength = [double]$property.Value }
    }
    if ($null -eq $valueLength) { $valueLength = $keyLength }
    if (-not $layers -or -not $heads -or -not $keyLength -or -not $valueLength) {
        return [int64][Math]::Min($Maximum, 4096)
    }

    # Conservative CPU/RAM estimate: model weights + FP16 KV cache + 10% overhead,
    # while preserving 4 GiB for Windows and other applications.
    $bytesPerToken = $layers * $heads * ($keyLength + $valueLength) * 2
    $budgetGiB = [Math]::Max(0, $freeRamGiB - 4)
    $cacheBudgetBytes = [Math]::Max(0, ($budgetGiB - $weightsGiB - [Math]::Max(0.5, $weightsGiB * 0.10)) * 1GB)
    if ($bytesPerToken -le 0 -or $cacheBudgetBytes -le 0) {
        return [int64][Math]::Min($Maximum, 2048)
    }

    $fit = [int64][Math]::Floor($cacheBudgetBytes / $bytesPerToken)
    $selected = [int64]512
    foreach ($candidate in @(1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144, 524288, 1048576)) {
        if ($candidate -le $Maximum -and $candidate -le $fit) {
            $selected = [int64]$candidate
        }
    }
    return $selected
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
    $maximum = Get-MaximumContext -Info $modelInfo
    if ($null -eq $maximum -or $selectedContext -gt $maximum) {
        throw "The persisted Codex context $selectedContext exceeds or cannot be verified against the model maximum."
    }
} else {
    if ($Model -notmatch '^[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9._-]+)?$') {
        throw "Model name contains unsupported characters; refusing to construct a persistent Codex model variant."
    }

    $baseModel = $Model
    $modelInfo = Get-ModelInfo -ModelName $baseModel
    $maximum = Get-MaximumContext -Info $modelInfo
    if ($null -eq $maximum) {
        throw "Ollama did not publish a verifiable context_length for '$baseModel'; Codex cannot select a safe context."
    }

    $manualContext = [string]$env:AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH
    if (-not [string]::IsNullOrWhiteSpace($manualContext)) {
        $selectedContext = 0L
        if (-not [int64]::TryParse($manualContext, [ref]$selectedContext) -or $selectedContext -lt 512) {
            throw "AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH must be an integer of at least 512."
        }
        if ($selectedContext -gt $maximum) {
            throw "Requested context $selectedContext exceeds model '$baseModel' maximum $maximum."
        }
        $selectionMode = "manual"
    } else {
        $selectedContext = Get-AutomaticContext -Maximum $maximum -Info $modelInfo
        $selectionMode = "automatic"
    }

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
