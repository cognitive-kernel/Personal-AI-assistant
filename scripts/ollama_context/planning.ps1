function Get-AutomaticContext {
    param(
        [Parameter(Mandatory = $true)]$Info,
        [Parameter(Mandatory = $true)][string]$ModelName,
        [Nullable[long]]$RequestedContext
    )

    Write-Host "[ollama-timing] start phase=hardware-context-planning"
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    try {
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
            $runtime = Invoke-OllamaApi -Path "/api/ps" -Method Get -TimeoutSec 10
        } catch {
            throw "Could not inspect currently loaded Ollama model memory before context planning. $($_.Exception.Message)"
        }
        $runningMatches = @($runtime.models | Where-Object {
            $_ -and $_.name -and (
                ([string]$_.name).Equals($ModelName, [System.StringComparison]::OrdinalIgnoreCase) -or
                ([string]$_.name) -match ("(?i)^" + [regex]::Escape($ModelName) + "-codex-ctx[0-9]+$")
            )
        })
        if ($runningMatches.Count -gt 1) {
            throw "Multiple instances of '$ModelName' are loaded; refusing to infer a single safe memory budget."
        }
        $modelIsLoaded = $runningMatches.Count -eq 1
        $runningContextTokens = $null
        if ($modelIsLoaded) {
            $parsedRunningContext = 0L
            if (-not [int64]::TryParse([string]$runningMatches[0].context_length, [ref]$parsedRunningContext) -or $parsedRunningContext -le 0) {
                throw "The loaded model does not expose its allocated positive context length."
            }
            $runningContextTokens = $parsedRunningContext
        }

        try {
            $tags = Invoke-OllamaApi -Path "/api/tags" -Method Get -TimeoutSec 10
        } catch {
            throw "Could not inspect installed Ollama model sizes for context planning. $($_.Exception.Message)"
        }
        $tag = @($tags.models | Where-Object {
            $_.name -and ([string]$_.name).Equals($ModelName, [System.StringComparison]::OrdinalIgnoreCase)
        }) | Select-Object -First 1
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
            model_is_loaded = $modelIsLoaded
            running_context_tokens = $runningContextTokens
        } | ConvertTo-Json -Depth 30 -Compress
        $helperPath = Join-Path (Split-Path -Parent $script:OllamaScriptsRoot) "app\ollama_context.py"
        if (-not (Test-Path -LiteralPath $helperPath -PathType Leaf)) {
            throw "The shared Ollama context-selection module is missing: $helperPath"
        }
        $selectionPath = Join-Path $env:TEMP ("ollama-context-selection-" + [guid]::NewGuid().ToString("N") + ".json")
        try {
            [System.IO.File]::WriteAllText($selectionPath, $selectionInput, [System.Text.UTF8Encoding]::new($false))
            $selectionOutput = & python $helperPath --input-file $selectionPath
            $exitCode = $LASTEXITCODE
            if ($exitCode -ne 0) {
                throw "Fail-closed Ollama context selection failed (exit=$exitCode); see the metadata/host diagnostic above."
            }
            try {
                $selection = ($selectionOutput -join [Environment]::NewLine) | ConvertFrom-Json -ErrorAction Stop
            } catch {
                throw "The Ollama context-selection module returned invalid JSON."
            }
        } finally {
            if (Test-Path -LiteralPath $selectionPath) {
                Remove-Item -LiteralPath $selectionPath -Force -ErrorAction SilentlyContinue
            }
        }
        if ($null -eq $selection.selected_context_tokens -or [int64]$selection.selected_context_tokens -le 0) {
            throw "The Ollama context-selection module did not return a positive hardware-safe Codex context."
        }
        return $selection
    } finally {
        Write-OllamaPhaseTiming -Phase "hardware-context-planning" -Timer $timer
    }
}
