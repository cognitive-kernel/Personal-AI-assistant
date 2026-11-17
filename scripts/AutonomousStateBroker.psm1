Set-StrictMode -Version Latest

function Invoke-AutonomousStateBroker {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][ValidateSet("GET", "POST")][string]$Method,
        [Parameter(Mandatory=$true)][ValidatePattern("^/v1/[A-Za-z0-9/_-]+$")][string]$Path,
        [int]$TimeoutMilliseconds = 3500
    )

    $pipe = $null
    $reader = $null
    $writer = $null
    try {
        $pipe = [System.IO.Pipes.NamedPipeClientStream]::new(
            ".",
            "PersonalAIAssistant.StateBroker.v1",
            [System.IO.Pipes.PipeDirection]::InOut,
            [System.IO.Pipes.PipeOptions]::None,
            [System.Security.Principal.TokenImpersonationLevel]::Impersonation
        )
        $pipe.Connect($TimeoutMilliseconds)
        $pipe.ReadTimeout = $TimeoutMilliseconds
        $pipe.WriteTimeout = $TimeoutMilliseconds
        $reader = [System.IO.StreamReader]::new($pipe, [System.Text.Encoding]::UTF8, $false, 4096, $true)
        $writer = [System.IO.StreamWriter]::new($pipe, [System.Text.UTF8Encoding]::new($false), 4096, $true)
        $writer.AutoFlush = $true
        $request = @{
            version = 1
            method = $Method.ToUpperInvariant()
            path = $Path
        } | ConvertTo-Json -Compress
        $writer.WriteLine($request)
        $responseLine = $reader.ReadLine()
        if ([string]::IsNullOrWhiteSpace($responseLine)) {
            throw "State broker returned an empty response."
        }
        $envelope = $responseLine | ConvertFrom-Json -ErrorAction Stop
        if ($null -eq $envelope -or $envelope.version -ne 1 -or $envelope.status -lt 200 -or $envelope.status -ge 300) {
            $code = if ($envelope.payload.error) { [string]$envelope.payload.error } else { "invalid_response" }
            throw "State broker request failed ($($envelope.status): $code)."
        }
        return $envelope.payload
    } finally {
        if ($writer) { $writer.Dispose() }
        if ($reader) { $reader.Dispose() }
        if ($pipe) { $pipe.Dispose() }
    }
}

Export-ModuleMember -Function Invoke-AutonomousStateBroker
