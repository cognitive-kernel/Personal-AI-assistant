Set-StrictMode -Version Latest

function Invoke-AutonomousStateBroker {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][ValidateSet("GET", "POST")][string]$Method,
        [Parameter(Mandatory=$true)][ValidatePattern("^/v1/[A-Za-z0-9/_-]+$")][string]$Path,
        [int]$TimeoutMilliseconds = 3500
    )

    $allowed = (
        $Method -eq "GET" -and $Path -in @("/v1/health", "/v1/health/verify", "/v1/pending-work", "/v1/progress")
    ) -or (
        $Method -eq "GET" -and $Path -match "^/v1/diagnostics/[0-9]{1,20}$"
    ) -or (
        $Method -eq "POST" -and $Path -eq "/v1/cleanup"
    )
    if (-not $allowed) {
        throw "Unsupported state-broker endpoint."
    }
    if ($TimeoutMilliseconds -lt 250 -or $TimeoutMilliseconds -gt 30000) {
        throw "State-broker timeout must be between 250 and 30000 milliseconds."
    }

    $pipe = $null
    $reader = $null
    $writer = $null
    try {
        $pipe = [System.IO.Pipes.NamedPipeClientStream]::new(
            ".",
            "PersonalAIAssistant.StateBroker.v1",
            [System.IO.Pipes.PipeDirection]::InOut,
            [System.IO.Pipes.PipeOptions]::Asynchronous,
            [System.Security.Principal.TokenImpersonationLevel]::Impersonation
        )
        $deadline = [DateTime]::UtcNow.AddMilliseconds($TimeoutMilliseconds)
        $pipe.Connect($TimeoutMilliseconds)
        $reader = [System.IO.StreamReader]::new($pipe, [System.Text.Encoding]::UTF8, $false, 4096, $true)
        $writer = [System.IO.StreamWriter]::new($pipe, [System.Text.UTF8Encoding]::new($false), 4096, $true)
        $writer.AutoFlush = $true
        $request = @{
            version = 1
            method = $Method.ToUpperInvariant()
            path = $Path
        } | ConvertTo-Json -Compress

        $remaining = [int][Math]::Floor(($deadline - [DateTime]::UtcNow).TotalMilliseconds)
        if ($remaining -le 0 -or -not $writer.WriteLineAsync($request).Wait([TimeSpan]::FromMilliseconds([Math]::Max(1, $remaining)))) {
            throw "State broker request timed out while writing."
        }
        $remaining = [int][Math]::Floor(($deadline - [DateTime]::UtcNow).TotalMilliseconds)
        if ($remaining -le 0) {
            throw "State broker request timed out while waiting for a response."
        }
        $readTask = $reader.ReadLineAsync()
        if (-not $readTask.Wait([TimeSpan]::FromMilliseconds($remaining))) {
            throw "State broker request timed out while reading a response."
        }
        $responseLine = $readTask.GetAwaiter().GetResult()
        if ([string]::IsNullOrWhiteSpace($responseLine)) {
            throw "State broker returned an empty response."
        }
        $envelope = $responseLine | ConvertFrom-Json -ErrorAction Stop
        if ($null -eq $envelope -or $envelope.version -ne 1) {
            throw "State broker returned an invalid response envelope."
        }
        $status = [int]$envelope.status
        $payload = $envelope.payload
        if ($status -lt 200 -or $status -ge 300) {
            $code = "invalid_response"
            if ($null -ne $payload) {
                $errorProperty = $payload.PSObject.Properties["error"]
                if ($null -ne $errorProperty -and -not [string]::IsNullOrWhiteSpace([string]$errorProperty.Value)) {
                    $code = [string]$errorProperty.Value
                }
            }

            $healthDetails = @()
            foreach ($fieldName in @("ready", "storage_ready", "acl_ready", "schema_version")) {
                if ($null -ne $payload) {
                    $field = $payload.PSObject.Properties[$fieldName]
                    if ($null -ne $field) {
                        $healthDetails += ($fieldName + "=" + [string]$field.Value)
                    }
                }
            }
            $detail = if ($healthDetails.Count -gt 0) {
                " Health: " + ($healthDetails -join ", ") + "."
            } else {
                ""
            }
            throw "State broker request failed ($status: $code).$detail"
        }
        return $payload
    } finally {
        # The service closes its pipe instance after each response. Disposing a
        # writer after that close can surface a harmless broken-pipe flush error.
        if ($pipe) { try { $pipe.Dispose() } catch { } }
        if ($writer) { try { $writer.Dispose() } catch { } }
        if ($reader) { try { $reader.Dispose() } catch { } }
    }
}

Export-ModuleMember -Function Invoke-AutonomousStateBroker
