$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$helperPath = Join-Path (Split-Path -Parent $PSScriptRoot) "app\ollama_context.py"
if (-not (Test-Path -LiteralPath $helperPath -PathType Leaf)) {
    throw "Ollama context selector was not found: $helperPath"
}

$inputPath = Join-Path $env:TEMP ("ollama-context-bridge-" + [guid]::NewGuid().ToString("N") + ".json")
$errorPath = "$inputPath.stderr"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
try {
    $payload = @{
        architecture = "llama"
        model_info = @{
            "llama.context_length" = 131072
            "llama.block_count" = 32
            "llama.attention.head_count_kv" = 8
            "llama.attention.key_length" = 128
            "llama.attention.value_length" = 128
        }
        model_weight_bytes = [long](5 * 1GB)
        available_memory_bytes = [long](20 * 1GB)
        requested_context_tokens = 100000
    } | ConvertTo-Json -Depth 20 -Compress

    [IO.File]::WriteAllText($inputPath, $payload, $utf8NoBom)
    $output = & python $helperPath --input-file $inputPath
    if ($LASTEXITCODE -ne 0) {
        throw "Python context selector rejected valid UTF-8 JSON: $($output -join ' ')"
    }
    $selection = ($output -join [Environment]::NewLine) | ConvertFrom-Json -ErrorAction Stop
    if ([int64]$selection.selected_context_tokens -ne 86016) {
        throw "Expected safe 86016-token selection; received $($selection.selected_context_tokens)."
    }

    $payloadObject = $payload | ConvertFrom-Json
    $payloadObject.available_memory_bytes = [long](10 * 1GB)
    [IO.File]::WriteAllText($inputPath, ($payloadObject | ConvertTo-Json -Depth 20 -Compress), $utf8NoBom)
    $output = & python $helperPath --input-file $inputPath 2> $errorPath
    $helperExitCode = $LASTEXITCODE
    $helperError = if (Test-Path -LiteralPath $errorPath) { Get-Content -LiteralPath $errorPath -Raw } else { "" }
    if ($helperExitCode -eq 0 -or $helperError -notmatch "minimum 64000-token") {
        throw "Insufficient RAM must fail closed instead of returning a low-context fallback. $helperError"
    }

    $payloadObject.available_memory_bytes = [long](16 * 1GB)
    $payloadObject.model_weights_already_loaded = $true
    [IO.File]::WriteAllText($inputPath, ($payloadObject | ConvertTo-Json -Depth 20 -Compress), $utf8NoBom)
    $output = & python $helperPath --input-file $inputPath
    if ($LASTEXITCODE -ne 0) {
        throw "Loaded model weights should not be double-counted: $($output -join ' ')"
    }
    $selection = ($output -join [Environment]::NewLine) | ConvertFrom-Json -ErrorAction Stop
    if ([int64]$selection.selected_context_tokens -ne 94208) {
        throw "Expected 94208 tokens when model weights are already resident; received $($selection.selected_context_tokens)."
    }
} finally {
    foreach ($temporaryPath in @($inputPath, $errorPath)) {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host "Windows PowerShell 5.1 to Python UTF-8 context bridge and fail-closed selection passed."
