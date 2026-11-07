[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Model,
    [ValidateRange(64000, 131072)]
    [int]$MinimumContextLength = 64000,
    [ValidateRange(30, 900)]
    [int]$TimeoutSeconds = 600,
    [switch]$VerifyOnly
)

$ErrorActionPreference = "Stop"
$baseUrl = "http://127.0.0.1:11434"

function Get-AllocatedContext {
    param([Parameter(Mandatory = $true)][string]$ModelName)

    try {
        $runtime = Invoke-RestMethod -Uri "$baseUrl/api/ps" -Method Get -TimeoutSec 10
    } catch {
        throw "Could not read Ollama's loaded-model context from /api/ps. $($_.Exception.Message)"
    }

    $loadedModels = @($runtime.models | Where-Object {
        $_ -and $_.name -and ([string]$_.name).Equals($ModelName, [System.StringComparison]::OrdinalIgnoreCase)
    })
    if ($loadedModels.Count -ne 1) {
        throw "Ollama did not report exactly one loaded instance of '$ModelName' in /api/ps."
    }

    $contextLength = 0L
    if ($null -eq $loadedModels[0].context_length -or -not [int64]::TryParse([string]$loadedModels[0].context_length, [ref]$contextLength)) {
        throw "Ollama /api/ps did not report a valid allocated context length for '$ModelName'."
    }
    return $contextLength
}

if (-not $VerifyOnly) {
    # Build a persistent, isolated Ollama model variant. Request-level num_ctx
    # only affects the warm-up request; Modelfile PARAMETER num_ctx is inherited
    # by every later Codex request without requiring an Ollama service restart.
    if ($Model -notmatch '^[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9._-]+)?$') {
        throw "Model name contains unsupported characters; refusing to construct a persistent Codex model variant."
    }
    $codexModel = "$Model-codex-64k"
    $createBody = @{
        model = $codexModel
        from = $Model
        parameters = @{ num_ctx = $MinimumContextLength }
        stream = $false
    } | ConvertTo-Json -Depth 5 -Compress

    try {
        $createResult = Invoke-RestMethod -Uri "$baseUrl/api/create" -Method Post -ContentType "application/json" -Body $createBody -TimeoutSec $TimeoutSeconds
    } catch {
        throw "Ollama could not create the persistent 64k Codex model '$codexModel' from '$Model'. Check available memory and model compatibility. $($_.Exception.Message)"
    }

    try {
        $showBody = @{ model = $codexModel; verbose = $true } | ConvertTo-Json -Compress
        $modelInfo = Invoke-RestMethod -Uri "$baseUrl/api/show" -Method Post -Body $showBody -ContentType "application/json" -TimeoutSec 20
    } catch {
        throw "Could not inspect the persistent Codex model '$codexModel'. $($_.Exception.Message)"
    }
    $numCtx = [regex]::Match([string]$modelInfo.modelfile, '(?im)^\s*PARAMETER\s+num_ctx\s+(\d+)\s*$')
    if (-not $numCtx.Success -or [int64]$numCtx.Groups[1].Value -lt $MinimumContextLength) {
        throw "Persistent model '$codexModel' does not declare PARAMETER num_ctx $MinimumContextLength; refusing to start Codex."
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
        throw "Ollama could not allocate the persistent 64k Codex model '$codexModel'. Check host memory. $($_.Exception.Message)"
    }

    if ($env:GITHUB_ENV) {
        Add-Content -LiteralPath $env:GITHUB_ENV -Value "AUTONOMOUS_ENGINEERING_MODEL=$codexModel" -Encoding utf8
    }
} else {
    $codexModel = $Model
    try {
        $showBody = @{ model = $codexModel; verbose = $true } | ConvertTo-Json -Compress
        $modelInfo = Invoke-RestMethod -Uri "$baseUrl/api/show" -Method Post -Body $showBody -ContentType "application/json" -TimeoutSec 20
    } catch {
        throw "Could not inspect the Codex model '$codexModel' after execution. $($_.Exception.Message)"
    }
    $numCtx = [regex]::Match([string]$modelInfo.modelfile, '(?im)^\s*PARAMETER\s+num_ctx\s+(\d+)\s*$')
    if (-not $numCtx.Success -or [int64]$numCtx.Groups[1].Value -lt $MinimumContextLength) {
        throw "Codex model '$codexModel' no longer declares the required persistent context."
    }
}

$contextLength = Get-AllocatedContext -ModelName $codexModel
Write-Host "Ollama base model: $Model"
Write-Host "Persistent Codex model: $codexModel"
Write-Host "Ollama allocated runtime context: $contextLength tokens"
Write-Host "Codex minimum required context: $MinimumContextLength tokens"
if ($contextLength -lt $MinimumContextLength) {
    throw "Ollama allocated only $contextLength tokens for '$codexModel'; Codex requires at least $MinimumContextLength. No Codex task may proceed."
}
if (-not $VerifyOnly) {
    Write-Host "AUTONOMOUS_ENGINEERING_MODEL=$codexModel"
}
Write-Host "OLLAMA_RUNTIME_CONTEXT_OK"
