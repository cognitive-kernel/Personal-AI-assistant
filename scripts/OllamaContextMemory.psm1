Set-StrictMode -Version Latest

function Get-OllamaCacheBudgetBytes {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][double]$FreeRamGiB,
        [Parameter(Mandatory = $true)][double]$WeightsGiB
    )

    foreach ($entry in @(
        @{ Name = "FreeRamGiB"; Value = $FreeRamGiB },
        @{ Name = "WeightsGiB"; Value = $WeightsGiB }
    )) {
        if ([double]::IsNaN($entry.Value) -or [double]::IsInfinity($entry.Value) -or $entry.Value -lt 0) {
            throw "$($entry.Name) must be a finite, non-negative number."
        }
    }

    # Keep the arithmetic in Double throughout. Passing an Int32 zero to
    # [Math]::Max can select the Int32 overload and overflow on multi-GiB values.
    $budgetGiB = [Math]::Max([double]0, [double]($FreeRamGiB - [double]4))
    $overheadGiB = [Math]::Max([double]0.5, [double]($WeightsGiB * [double]0.10))
    $cacheGiB = [double]($budgetGiB - $WeightsGiB - $overheadGiB)
    if ($cacheGiB -le 0) {
        return [double]0
    }
    return [double]($cacheGiB * [double]1GB)
}

Export-ModuleMember -Function Get-OllamaCacheBudgetBytes
