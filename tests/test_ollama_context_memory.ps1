$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$modulePath = Join-Path (Split-Path -Parent $PSScriptRoot) "scripts\OllamaContextMemory.psm1"
Import-Module $modulePath -Force

$budget = Get-OllamaCacheBudgetBytes -FreeRamGiB 15.5 -WeightsGiB 5.0
$expected = [double]6442450944
if ($budget -isnot [double]) {
    throw "Cache budget must remain a Double; received $($budget.GetType().FullName)."
}
if ($budget -ne $expected) {
    throw "Expected a 6-GiB cache budget ($expected bytes), received $budget."
}
if ($budget -le [int32]::MaxValue) {
    throw "The regression input must exercise a value above the Int32 range."
}

$insufficient = Get-OllamaCacheBudgetBytes -FreeRamGiB 4.0 -WeightsGiB 5.0
if ($insufficient -ne [double]0) {
    throw "Insufficient RAM must produce a zero cache budget, received $insufficient."
}

$rejected = $false
try {
    Get-OllamaCacheBudgetBytes -FreeRamGiB ([double]::PositiveInfinity) -WeightsGiB 1.0 | Out-Null
} catch {
    $rejected = $true
}
if (-not $rejected) {
    throw "Non-finite memory telemetry must be rejected."
}

Write-Host "Ollama context memory arithmetic regression passed."
