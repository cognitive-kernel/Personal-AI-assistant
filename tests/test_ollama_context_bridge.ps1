$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$helperPath = Join-Path (Split-Path -Parent $PSScriptRoot) "app\ollama_context.py"
if (-not (Test-Path -LiteralPath $helperPath -PathType Leaf)) {
    throw "Ollama context selector was not found: $helperPath"
}

$inputPath = Join-Path $env:TEMP ("ollama-context-bridge-" + [guid]::NewGuid().ToString("N") + ".json")
$outputPath = "$inputPath.stdout"
$errorPath = "$inputPath.stderr"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Invoke-ContextSelector {
    foreach ($temporaryPath in @($outputPath, $errorPath)) {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
    }
    $pythonPath = (Get-Command python -ErrorAction Stop).Source
    $argumentList = '"' + $helperPath + '" --input-file "' + $inputPath + '"'
    $process = Start-Process -FilePath $pythonPath -ArgumentList $argumentList -NoNewWindow -PassThru -Wait -RedirectStandardOutput $outputPath -RedirectStandardError $errorPath
    return [int]$process.ExitCode
}

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
        requested_context_tokens = [long]100000
    } | ConvertTo-Json -Depth 20 -Compress
    [IO.File]::WriteAllText($inputPath, $payload, $utf8NoBom)

    $helperExitCode = Invoke-ContextSelector
    if ($helperExitCode -ne 0) {
        $helperError = if (Test-Path -LiteralPath $errorPath) { [IO.File]::ReadAllText($errorPath, [Text.Encoding]::UTF8) } else { "" }
        throw "The UTF-8 file bridge did not return a valid context selection: $helperError"
    }
    $selection = [IO.File]::ReadAllText($outputPath, [Text.Encoding]::UTF8) | ConvertFrom-Json -ErrorAction Stop
    if ([int64]$selection.selected_context_tokens -ne 86016) {
        throw "Expected 86016 safe tokens, received $($selection.selected_context_tokens)."
    }

    $payloadObject = $payload | ConvertFrom-Json
    $payloadObject.available_memory_bytes = [long](10 * 1GB)
    [IO.File]::WriteAllText($inputPath, ($payloadObject | ConvertTo-Json -Depth 20 -Compress), $utf8NoBom)
    $helperExitCode = Invoke-ContextSelector
    $helperError = if (Test-Path -LiteralPath $errorPath) { [IO.File]::ReadAllText($errorPath, [Text.Encoding]::UTF8) } else { "" }
    if ($helperExitCode -eq 0 -or $helperError -notmatch "minimum 64000-token") {
        throw "Insufficient RAM must fail closed instead of returning a low-context fallback. $helperError"
    }

    $payloadObject.available_memory_bytes = [long](16 * 1GB)
    $payloadObject | Add-Member -NotePropertyName model_weights_already_loaded -NotePropertyValue $true -Force
    [IO.File]::WriteAllText($inputPath, ($payloadObject | ConvertTo-Json -Depth 20 -Compress), $utf8NoBom)
    $helperExitCode = Invoke-ContextSelector
    if ($helperExitCode -ne 0) {
        $helperError = if (Test-Path -LiteralPath $errorPath) { [IO.File]::ReadAllText($errorPath, [Text.Encoding]::UTF8) } else { "" }
        throw "Loaded model weights should not be double-counted: $helperError"
    }
    $selection = [IO.File]::ReadAllText($outputPath, [Text.Encoding]::UTF8) | ConvertFrom-Json -ErrorAction Stop
    if ([int64]$selection.selected_context_tokens -ne 94208) {
        throw "Expected 94208 tokens when model weights are already resident; received $($selection.selected_context_tokens)."
    }
} finally {
    foreach ($temporaryPath in @($inputPath, $outputPath, $errorPath)) {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host "Windows PowerShell 5.1 to Python UTF-8 context bridge and fail-closed selection passed."
