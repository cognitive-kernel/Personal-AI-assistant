Set-StrictMode -Version Latest

function Install-AutonomousStateBrokerService {
    $serviceName = "PersonalAIAssistantStateBroker"
    $serviceRoot = Join-Path $env:ProgramData "PersonalAIAssistant\StateBroker"
    $serviceSource = Join-Path $PSScriptRoot "AutonomousStateBrokerService.cs"
    if (-not (Test-Path -LiteralPath $serviceSource -PathType Leaf)) {
        throw "AutonomousStateBrokerService.cs is missing from the provisioning package."
    }
    New-Item -ItemType Directory -Path $serviceRoot -Force | Out-Null
    $serviceExe = Join-Path $serviceRoot "PersonalAIAssistant.StateBroker.exe"
    $temporaryExe = Join-Path $serviceRoot ("broker-build-" + [guid]::NewGuid().ToString("N") + ".exe")

    $frameworkCandidates = @(
        (Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319"),
        (Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319")
    )
    $frameworkRoot = $frameworkCandidates | Where-Object {
        Test-Path -LiteralPath (Join-Path $_ "csc.exe") -PathType Leaf
    } | Select-Object -First 1
    if (-not $frameworkRoot) {
        throw ".NET Framework C# compiler is required to install the local state broker."
    }
    $compiler = Join-Path $frameworkRoot "csc.exe"
    $references = @("System.ServiceProcess.dll", "System.Core.dll", "System.Web.Extensions.dll")
    $pipeAccessAssembly = Join-Path $frameworkRoot "System.IO.Pipes.AccessControl.dll"
    if (Test-Path -LiteralPath $pipeAccessAssembly -PathType Leaf) {
        $references += "System.IO.Pipes.AccessControl.dll"
    }
    $compileArguments = @("/nologo", "/target:exe", "/out:$temporaryExe")
    foreach ($reference in $references) {
        $compileArguments += "/reference:" + (Join-Path $frameworkRoot $reference)
    }
    $compileArguments += $serviceSource
    & $compiler @compileArguments
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $temporaryExe -PathType Leaf)) {
        Remove-Item -LiteralPath $temporaryExe -Force -ErrorAction SilentlyContinue
        throw "Could not compile the local Windows state broker; the Runner and existing state were not replaced."
    }

    $existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    $wasRunning = ($null -ne $existing -and $existing.Status -eq "Running")
    if ($wasRunning) {
        Stop-Service -Name $serviceName -Force -ErrorAction Stop
        $existing = Get-Service -Name $serviceName -ErrorAction Stop
        $existing.WaitForStatus(
            [System.ServiceProcess.ServiceControllerStatus]::Stopped,
            [TimeSpan]::FromSeconds(30)
        )
    }
    $backupExe = $null
    $newServiceCreated = $false
    try {
        if (Test-Path -LiteralPath $serviceExe -PathType Leaf) {
            $backupExe = Join-Path $serviceRoot ("broker-previous-" + [guid]::NewGuid().ToString("N") + ".exe")
            [System.IO.File]::Replace($temporaryExe, $serviceExe, $backupExe, $true)
        } else {
            [System.IO.File]::Move($temporaryExe, $serviceExe)
        }
        if (-not $existing) {
            New-Service -Name $serviceName -BinaryPathName ('"' + $serviceExe + '"') -DisplayName "Personal AI Assistant State Broker" -Description "Mediates allow-listed local access to protected autonomous-engineering state." -StartupType Automatic | Out-Null
            $newServiceCreated = $true
        }
        $sc = Get-Command sc.exe -ErrorAction Stop
        & $sc.Source config $serviceName 'binPath=' $serviceExe 'obj=' 'NT AUTHORITY\LocalService' 'start=' 'auto' | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Could not configure the state broker to run under its restricted LocalService identity."
        }
        $service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction Stop
        if ($service.StartName -ne "NT AUTHORITY\LocalService") {
            throw "The state broker must run under NT AUTHORITY\LocalService."
        }
        Start-Service -Name $serviceName -ErrorAction Stop
        $running = Get-Service -Name $serviceName -ErrorAction Stop
        $running.WaitForStatus(
            [System.ServiceProcess.ServiceControllerStatus]::Running,
            [TimeSpan]::FromSeconds(30)
        )

        Import-Module (Join-Path $PSScriptRoot "AutonomousStateBroker.psm1") -Force
        $health = Invoke-AutonomousStateBroker -Method GET -Path "/v1/health" -TimeoutMilliseconds 30000
        if ($null -eq $health -or $health.ready -ne $true -or
            $health.storage_ready -ne $true -or $health.acl_ready -ne $true -or
            $health.schema_version -ne 1) {
            throw "The state broker service started but failed its protected storage and ACL health checks."
        }
        if ($backupExe -and (Test-Path -LiteralPath $backupExe)) {
            Remove-Item -LiteralPath $backupExe -Force -ErrorAction Stop
        }
        Write-Host "Machine state broker installed and verified at $serviceRoot."
    } catch {
        $failure = $_
        $serviceAfterFailure = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
        if ($serviceAfterFailure -and $serviceAfterFailure.Status -eq "Running") {
            Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
        }
        if ($backupExe -and (Test-Path -LiteralPath $backupExe)) {
            try {
                [System.IO.File]::Replace($backupExe, $serviceExe, $null, $true)
            } catch {
                Write-Warning "The previous state-broker binary backup remains at $backupExe; do not delete it until the service is restored."
            }
        } elseif ($newServiceCreated) {
            Remove-Item -LiteralPath $serviceExe -Force -ErrorAction SilentlyContinue
            sc.exe delete $serviceName | Out-Null
        }
        if ($wasRunning) {
            Start-Service -Name $serviceName -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $temporaryExe) {
            Remove-Item -LiteralPath $temporaryExe -Force -ErrorAction SilentlyContinue
        }
        throw $failure
    }
}

Export-ModuleMember -Function Install-AutonomousStateBrokerService
