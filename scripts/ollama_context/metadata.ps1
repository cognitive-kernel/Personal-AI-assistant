function Get-ModelInfo {
    param([Parameter(Mandatory = $true)][string]$ModelName)

    $body = @{ model = $ModelName; verbose = $true } | ConvertTo-Json -Compress
    try {
        return Invoke-OllamaApi -Path "/api/show" -Method Post -Body $body -TimeoutSec 20
    } catch {
        $statusCode = 0
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        if ($statusCode -eq 404) {
            return $null
        }
        throw "Could not inspect Ollama model '$ModelName'. $($_.Exception.Message)"
    }
}

function Get-MaximumContext {
    param([Parameter(Mandatory = $true)]$Info)

    $architecture = [string]$Info.details.family
    $key = if ([string]::IsNullOrWhiteSpace($architecture)) { "" } else { $architecture + ".context_length" }
    $property = if ($key) { $Info.model_info.PSObject.Properties[$key] } else { $null }
    $maximum = 0L
    $validContext = $property -and [int64]::TryParse([string]$property.Value, [ref]$maximum) -and $maximum -gt 0
    if (-not $validContext) {
        $declaredArchitecture = $Info.model_info.PSObject.Properties["general.architecture"]
        if ($declaredArchitecture) {
            $architecture = [string]$declaredArchitecture.Value
            $key = $architecture + ".context_length"
            $property = $Info.model_info.PSObject.Properties[$key]
            $maximum = 0L
            $validContext = $property -and [int64]::TryParse([string]$property.Value, [ref]$maximum) -and $maximum -gt 0
        }
    }
    if (-not $validContext) {
        throw "Ollama did not publish a valid architecture-specific context limit ('$key')."
    }
    return $maximum
}

function Assert-ToolCallingCapability {
    param(
        [Parameter(Mandatory = $true)]$Info,
        [Parameter(Mandatory = $true)][string]$ModelName,
        [switch]$DeferLiveProbe
    )

    $capabilitiesProperty = $Info.PSObject.Properties["capabilities"]
    if ($capabilitiesProperty -and $null -ne $capabilitiesProperty.Value) {
        $capabilities = @($capabilitiesProperty.Value | ForEach-Object { [string]$_ })
        if ($capabilities -notcontains "tools") {
            throw "Selected Ollama model explicitly does not advertise the required 'tools' capability."
        }
        return $false
    }
    if ($DeferLiveProbe) {
        return $false
    }

    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    $toolProbeScript = Join-Path $PSScriptRoot "..\verify_ollama_tools.py"
    & python $toolProbeScript --model $ModelName --keep-alive 10m 2>&1 | ForEach-Object { Write-Host $_ }
    $exitCode = $LASTEXITCODE
    Write-OllamaPhaseTiming -Phase "structured-tool-probe" -Timer $timer
    if ($exitCode -ne 0) {
        throw "Selected Ollama model failed the live structured tool-call probe (exit=$exitCode)."
    }
    return $true
}

function Get-AllocatedContext {
    param([Parameter(Mandatory = $true)][string]$ModelName)

    try {
        $runtime = Invoke-OllamaApi -Path "/api/ps" -Method Get -TimeoutSec 10
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
