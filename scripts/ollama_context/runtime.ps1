function Start-OllamaModelWarmup {
    param(
        [Parameter(Mandatory = $true)][string]$ModelName,
        [Parameter(Mandatory = $true)][long]$ContextTokens,
        [ValidateRange(30, 900)][int]$TimeoutSeconds = 600
    )

    $body = @{
        model = $ModelName
        prompt = "Reply with OK."
        stream = $false
        keep_alive = "10m"
        options = @{ num_predict = 1; temperature = 0 }
    } | ConvertTo-Json -Depth 4 -Compress
    try {
        $null = Invoke-OllamaApi -Path "/api/generate" -Method Post -Body $body -TimeoutSec $TimeoutSeconds
    } catch {
        throw "Ollama could not load persistent Codex model '$ModelName' at context $ContextTokens. $($_.Exception.Message)"
    }
}
