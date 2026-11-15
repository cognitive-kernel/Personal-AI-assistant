Set-StrictMode -Version Latest

$script:AutonomousOllamaApiBaseUrl = "http://127.0.0.1:11434"

function ConvertFrom-AutonomousOllamaTagsResponse {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][AllowNull()][object]$Response)

    if ($null -eq $Response) {
        throw "Ollama /api/tags returned an empty response."
    }
    $modelsProperty = $Response.PSObject.Properties["models"]
    if (-not $modelsProperty -or $null -eq $modelsProperty.Value -or $modelsProperty.Value -is [string]) {
        throw "Ollama /api/tags returned an invalid model catalog."
    }

    $models = @($modelsProperty.Value)
    foreach ($model in $models) {
        $nameProperty = if ($null -ne $model) { $model.PSObject.Properties["name"] } else { $null }
        if (-not $nameProperty -or [string]::IsNullOrWhiteSpace([string]$nameProperty.Value)) {
            throw "Ollama /api/tags returned a model entry without a valid name."
        }
    }
    return $models
}

function Get-AutonomousOllamaModelCatalog {
    [CmdletBinding()]
    param([Parameter()][ValidateRange(1,60)][int]$TimeoutSeconds = 5)

    try {
        $response = Invoke-RestMethod -Uri "$script:AutonomousOllamaApiBaseUrl/api/tags" -TimeoutSec $TimeoutSeconds -MaximumRedirection 0
    } catch {
        throw "Ollama API is unavailable at 127.0.0.1:11434. $($_.Exception.Message)"
    }
    return (ConvertFrom-AutonomousOllamaTagsResponse -Response $response)
}

function Test-AutonomousOllamaModelInstalled {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$Model,
        [Parameter(Mandatory=$true)][AllowEmptyCollection()][object[]]$Catalog
    )

    $names = @($Catalog | ForEach-Object { [string]$_.name })
    return ($names -contains $Model)
}

Export-ModuleMember -Function ConvertFrom-AutonomousOllamaTagsResponse, Get-AutonomousOllamaModelCatalog, Test-AutonomousOllamaModelInstalled
