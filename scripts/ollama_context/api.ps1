function Invoke-OllamaApi {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][ValidateSet("Get", "Post")][string]$Method,
        [string]$Body,
        [ValidateRange(1, 900)][int]$TimeoutSec = 20
    )

    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $arguments = @{
            Uri = "$script:OllamaBaseUrl$Path"
            Method = $Method
            TimeoutSec = $TimeoutSec
            MaximumRedirection = 0
            ErrorAction = "Stop"
        }
        if ($PSBoundParameters.ContainsKey("Body")) {
            $arguments.Body = $Body
            $arguments.ContentType = "application/json"
        }
        return Invoke-RestMethod @arguments
    } finally {
        $timer.Stop()
        Write-Host ("[ollama-timing] method={0} path={1} elapsed_ms={2}" -f $Method, $Path, $timer.ElapsedMilliseconds)
    }
}

function Write-OllamaPhaseTiming {
    param(
        [Parameter(Mandatory = $true)][string]$Phase,
        [Parameter(Mandatory = $true)][System.Diagnostics.Stopwatch]$Timer
    )
    $Timer.Stop()
    Write-Host ("[ollama-timing] phase={0} elapsed_ms={1}" -f $Phase, $Timer.ElapsedMilliseconds)
}
