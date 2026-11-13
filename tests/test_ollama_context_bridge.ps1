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
    $pythonPath = (Get-Command python.exe -ErrorAction Stop).Source
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
        parallel_requests = [int]1
    } | ConvertTo-Json -Depth 20 -Compress
    [IO.File]::WriteAllText($inputPath, $payload, $utf8NoBom)

    $helperExitCode = Invoke-ContextSelector
# Exercise the pure PowerShell metadata helpers without requiring a live Ollama server.
if ((Resolve-OllamaParallelRequestCount -UserValue $null -MachineValue $null) -ne 1) {
    throw "An unset Ollama parallel request setting must use the documented default of one."
}
if ((Resolve-OllamaParallelRequestCount -UserValue "4" -MachineValue "2") -ne 4) {
    throw "The Ollama user-level parallel setting must take precedence over the machine setting."
}
if ((Resolve-OllamaParallelRequestCount -UserValue $null -MachineValue "3") -ne 3) {
    throw "The machine-level Ollama parallel setting was not resolved."
}
$cpuResidentModel = [pscustomobject]@{ size_vram = 0 }
if (-not (Test-OllamaWeightsAlreadyLoaded -LoadedModel $cpuResidentModel)) {
    throw "CPU-resident model weights should already be reflected in free physical RAM."
}
$gpuResidentModel = [pscustomobject]@{ size_vram = 1024 }
if (Test-OllamaWeightsAlreadyLoaded -LoadedModel $gpuResidentModel) {
    throw "VRAM-resident model weights must be charged conservatively to system RAM."
}
$unknownResidencyModel = [pscustomobject]@{}
if (Test-OllamaWeightsAlreadyLoaded -LoadedModel $unknownResidencyModel) {
    throw "Missing VRAM telemetry must not be treated as proof of CPU residency."
}
$invalidParallelRejected = $false
try {
    Resolve-OllamaParallelRequestCount -UserValue "invalid" -MachineValue $null | Out-Null
} catch {
    $invalidParallelRejected = $true
}
if (-not $invalidParallelRejected) {
    throw "Invalid OLLAMA_NUM_PARALLEL values must be rejected."
}
$invalidVramRejected = $false
try {
    Test-OllamaWeightsAlreadyLoaded -LoadedModel ([pscustomobject]@{ size_vram = -1 }) | Out-Null
} catch {
    $invalidVramRejected = $true
}
if (-not $invalidVramRejected) {
    throw "Negative size_vram telemetry must be rejected."
}

    if ($helperExitCode -ne 0) {
        $helperError = if (Test-Path -LiteralPath $errorPath) { [IO.File]::ReadAllText($errorPath, [Text.Encoding]::UTF8) } else { "" }
        throw "The UTF-8 file bridge did not return a valid context selection: $helperError"
    }
    $selection = [IO.File]::ReadAllText($outputPath, [Text.Encoding]::UTF8) | ConvertFrom-Json -ErrorAction Stop
    if ([int64]$selection.selected_context_tokens -ne 86016) {
        throw "Expected 86016 safe tokens, received $($selection.selected_context_tokens)."
    }

    # A context below the upstream 64k recommendation is best-effort, not a
    # hard application rejection, when model and host metadata support it.
    $payloadObject = $payload | ConvertFrom-Json
    $payloadObject.available_memory_bytes = [long](10 * 1GB)
    $payloadObject.requested_context_tokens = [long]1000
    [IO.File]::WriteAllText($inputPath, ($payloadObject | ConvertTo-Json -Depth 20 -Compress), $utf8NoBom)
    $helperExitCode = Invoke-ContextSelector
    if ($helperExitCode -ne 0) {
        $helperError = if (Test-Path -LiteralPath $errorPath) { [IO.File]::ReadAllText($errorPath, [Text.Encoding]::UTF8) } else { "" }
        throw "A supported positive context below 64k must remain selectable: $helperError"
    }
    $selection = [IO.File]::ReadAllText($outputPath, [Text.Encoding]::UTF8) | ConvertFrom-Json -ErrorAction Stop
    if ([int64]$selection.selected_context_tokens -ne 1000) {
        throw "Expected the requested 1000-token context to be preserved, received $($selection.selected_context_tokens)."
    }

    # Truly insufficient RAM must still fail closed rather than invent a fallback.
    $payloadObject.available_memory_bytes = [long](9.5 * 1GB)
    [IO.File]::WriteAllText($inputPath, ($payloadObject | ConvertTo-Json -Depth 20 -Compress), $utf8NoBom)
    $helperExitCode = Invoke-ContextSelector
    $helperError = if (Test-Path -LiteralPath $errorPath) { [IO.File]::ReadAllText($errorPath, [Text.Encoding]::UTF8) } else { "" }
    if ($helperExitCode -eq 0 -or $helperError -notmatch "Available RAM cannot hold the model weights") {
        throw "Insufficient RAM must fail closed instead of returning an unsafe context. $helperError"
    }

    # Free RAM already excludes resident model weights; do not charge them twice.
    $payloadObject.available_memory_bytes = [long](16 * 1GB)
    $payloadObject.requested_context_tokens = $null
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

    # Four configured parallel requests consume four times the KV-cache budget.
    $payloadObject.parallel_requests = [int]4
    $payloadObject.model_weights_already_loaded = $false
    $payloadObject.available_memory_bytes = [long](20 * 1GB)
    $payloadObject.model_weight_bytes = [long](5 * 1GB)
    $payloadObject.requested_context_tokens = [long]100000
    [IO.File]::WriteAllText($inputPath, ($payloadObject | ConvertTo-Json -Depth 20 -Compress), $utf8NoBom)
    $helperExitCode = Invoke-ContextSelector
    if ($helperExitCode -ne 0) {
        $helperError = if (Test-Path -LiteralPath $errorPath) { [IO.File]::ReadAllText($errorPath, [Text.Encoding]::UTF8) } else { "" }
        throw "Four-way parallel KV-cache budgeting failed: $helperError"
    }
    $selection = [IO.File]::ReadAllText($outputPath, [Text.Encoding]::UTF8) | ConvertFrom-Json -ErrorAction Stop
    if ([int64]$selection.selected_context_tokens -ne 21504) {
        throw "Expected 21504 tokens for four parallel requests; received $($selection.selected_context_tokens)."
    }
} finally {
    foreach ($temporaryPath in @($inputPath, $outputPath, $errorPath)) {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }
}

$readinessScripts = @(
    "scripts\check_autonomous_agent.ps1",
    "scripts\check_autonomous_runner.ps1",
    "scripts\provision_autonomous_runner.ps1",
    "scripts\verify_ollama_context.ps1",
    "scripts\ollama_context_helpers.ps1"
)
foreach ($relativePath in $readinessScripts) {
    $scriptPath = Join-Path (Split-Path -Parent $PSScriptRoot) $relativePath
    $tokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $scriptPath,
        [ref]$tokens,
        [ref]$parseErrors
    ) | Out-Null
    if ($parseErrors.Count -gt 0) {
        throw "PowerShell 5.1 parser rejected $relativePath - $($parseErrors[0].Message)"
    }
}

Write-Host "Windows PowerShell 5.1 to Python UTF-8 context bridge and adaptive fail-closed selection passed."
