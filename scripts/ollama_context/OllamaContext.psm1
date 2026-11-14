$script:OllamaBaseUrl = "http://127.0.0.1:11434"
$script:OllamaScriptsRoot = Split-Path -Parent $PSScriptRoot

. (Join-Path $PSScriptRoot "api.ps1")
. (Join-Path $PSScriptRoot "metadata.ps1")
. (Join-Path $PSScriptRoot "planning.ps1")
. (Join-Path $PSScriptRoot "runtime.ps1")

Export-ModuleMember -Function @(
    "Get-ModelInfo",
    "Get-MaximumContext",
    "Assert-ToolCallingCapability",
    "Get-AutomaticContext",
    "Get-AllocatedContext",
    "Start-OllamaModelWarmup"
)
