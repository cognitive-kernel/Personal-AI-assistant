[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Model,
    [ValidateRange(64000, 131072)]
    [int]$MinimumContextLength = 64000,
    [ValidateRange(30, 900)]
    [int]$TimeoutSeconds = 600
)
$ErrorActionPreference = "Stop"
$baseUrl = "http://127.0.0.1:11434"
$warmupBody = @{
    model = $Model
    prompt = "Reply with OK."
    stream = $false
    keep_alive = "10m"
    options = @{ num_ctx = $MinimumContextLength }
} | ConvertTo-Json -Depth 4 -Compress
try {
    $null = Invoke-RestMethod -Uri "$baseUrl/api/generate" -Method Post -ContentType "application/json" -Body $warmupBody -TimeoutSec $TimeoutSeconds
} catch {
    throw "Ollama could not load model '$Model' for the context preflight. Check model availability and host memory. $($_.Exception.Message)"
}
try {
    $runtime = Invoke-RestMethod -Uri "$baseUrl/api/ps" -Method Get -TimeoutSec 10
} catch {
    throw "Could not read Ollama's loaded-model context from /api/ps. $($_.Exception.Message)"
}
$loadedModels = @($runtime.models | Where-Object {
    $_ -and $_.name -and ([string]$_.name).Equals($Model, [System.StringComparison]::OrdinalIgnoreCase)
})
if ($loadedModels.Count -ne 1) {
    throw "Ollama did not report exactly one loaded instance of '$Model' in /api/ps; refusing to start Codex."
}
$contextLength = 0L
if ($null -eq $loadedModels[0].context_length -or -not [int64]::TryParse([string]$loadedModels[0].context_length, [ref]$contextLength)) {
    throw "Ollama /api/ps did not report a valid allocated context length for '$Model'; refusing to start Codex."
}
Write-Host "Ollama model: $Model"
Write-Host "Ollama allocated runtime context: $contextLength tokens"
Write-Host "Codex minimum required context: $MinimumContextLength tokens"
if ($contextLength -lt $MinimumContextLength) {
    throw "Ollama allocated only $contextLength tokens for '$Model' after explicitly requesting $MinimumContextLength via options.num_ctx; Codex requires at least $MinimumContextLength. In Ollama Settings -> Context Length set at least 64k, restart/reload the Ollama server and model, then verify with 'ollama ps'. Model metadata maximum is not the active context. No Codex task was started."
}
Write-Host "OLLAMA_RUNTIME_CONTEXT_OK"
