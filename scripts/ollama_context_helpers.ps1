function Resolve-OllamaParallelRequestCount {
    [CmdletBinding()]
    param(
        [AllowNull()][object]$UserValue,
        [AllowNull()][object]$MachineValue
    )

    if ($null -ne $UserValue) {
        $rawValue = [string]$UserValue
    } elseif ($null -ne $MachineValue) {
        $rawValue = [string]$MachineValue
    } else {
        $rawValue = $null
    }

    if ([string]::IsNullOrWhiteSpace($rawValue)) {
        # Ollama defaults to one parallel request when the setting is unset.
        $rawValue = "1"
    }
    $parallelRequests = [uint64]0
    if (-not [uint64]::TryParse($rawValue.Trim(), [ref]$parallelRequests) -or $parallelRequests -lt 1) {
        throw "OLLAMA_NUM_PARALLEL must be a positive integer."
    }
    return $parallelRequests
}

function Test-OllamaWeightsAlreadyLoaded {
    [CmdletBinding()]
    param([AllowNull()][object]$LoadedModel)

    if ($null -eq $LoadedModel) {
        return $false
    }
    $sizeVramProperty = $LoadedModel.PSObject.Properties["size_vram"]
    if ($null -eq $sizeVramProperty -or $null -eq $sizeVramProperty.Value) {
        # Missing VRAM telemetry is not proof that system RAM already accounts
        # for the weights; charge the full model size conservatively.
        return $false
    }
    $sizeVramBytes = 0L
    if (-not [int64]::TryParse([string]$sizeVramProperty.Value, [ref]$sizeVramBytes) -or $sizeVramBytes -lt 0) {
        throw "Ollama /api/ps reported invalid size_vram telemetry."
    }
    # Only a CPU-resident model (zero VRAM allocation) is already reflected in
    # Win32_OperatingSystem.FreePhysicalMemory.
    return ($sizeVramBytes -eq 0)
}
