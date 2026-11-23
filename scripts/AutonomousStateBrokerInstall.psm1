Set-StrictMode -Version Latest

function Get-AutonomousDotNetFrameworkRelease {
    $highestRelease = 0
    foreach ($view in @(
        [Microsoft.Win32.RegistryView]::Registry64,
        [Microsoft.Win32.RegistryView]::Registry32
    )) {
        $baseKey = $null
        $frameworkKey = $null
        try {
            $baseKey = [Microsoft.Win32.RegistryKey]::OpenBaseKey(
                [Microsoft.Win32.RegistryHive]::LocalMachine,
                $view
            )
            $frameworkKey = $baseKey.OpenSubKey(("SOFTWARE" + [char]92 + "Microsoft" + [char]92 + "NET Framework Setup" + [char]92 + "NDP" + [char]92 + "v4" + [char]92 + "Full"))
            if ($null -ne $frameworkKey) {
                $releaseValue = $frameworkKey.GetValue("Release", $null)
                if ($null -ne $releaseValue -and [int]$releaseValue -gt $highestRelease) {
                    $highestRelease = [int]$releaseValue
                }
            }
        } catch {
            # The unavailable registry view is expected on 32-bit Windows.
        } finally {
            if ($null -ne $frameworkKey) { $frameworkKey.Dispose() }
            if ($null -ne $baseKey) { $baseKey.Dispose() }
        }
    }
    return $highestRelease
}

function Test-AutonomousDotNetFramework48 {
    return (Get-AutonomousDotNetFrameworkRelease) -ge 528040
}

function Get-AutonomousStateBrokerPackage {
    $programData = [string]$env:ProgramData
    if ([string]::IsNullOrWhiteSpace($programData) -or
        -not [System.IO.Path]::IsPathRooted($programData)) {
        throw "The Windows CommonApplicationData location is unavailable; no machine state was changed."
    }

    $packageRoot = Join-Path $PSScriptRoot "bin"
    $packagedExe = Join-Path $packageRoot "PersonalAIAssistant.StateBroker.exe"
    $hashFile = $packagedExe + ".sha256"
    if (-not (Test-Path -LiteralPath $packagedExe -PathType Leaf) -or
        -not (Test-Path -LiteralPath $hashFile -PathType Leaf)) {
        throw "The prebuilt Windows state broker package or its SHA-256 manifest is missing."
    }

    $expectedHash = (Get-Content -LiteralPath $hashFile -Raw -ErrorAction Stop).Trim()
    if ($expectedHash -notmatch '^[0-9a-fA-F]{64}function Install-AutonomousStateBrokerService {
    $serviceName = "PersonalAIAssistantStateBroker"
    # Revalidate immediately before installation to prevent a package swap
    # between the setup script's read-only preflight and service mutation.
    $package = Get-AutonomousStateBrokerPackage
    $serviceRoot = $package.ServiceRoot
    $packagedExe = $package.Path
    $expectedHash = $package.Sha256
    $scCommand = Get-Command $package.ScCommand -ErrorAction Stop

    New-Item -ItemType Directory -Path $serviceRoot -Force | Out-Null
    $serviceExe = Join-Path $serviceRoot "PersonalAIAssistant.StateBroker.exe"
    $temporaryExe = Join-Path $serviceRoot ("broker-stage-" + [guid]::NewGuid().ToString("N") + ".exe")
    $existing = $null
    $wasRunning = $false
    try {
        Copy-Item -LiteralPath $packagedExe -Destination $temporaryExe -Force -ErrorAction Stop
        $copiedHash = (Get-FileHash -LiteralPath $temporaryExe -Algorithm SHA256 -ErrorAction Stop).Hash
        if (-not [string]::Equals($copiedHash, $expectedHash, [StringComparison]::OrdinalIgnoreCase)) {
            throw "The staged Windows state broker binary failed SHA-256 verification."
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
    } catch {
        Remove-Item -LiteralPath $temporaryExe -Force -ErrorAction SilentlyContinue
        if ($wasRunning) {
            Start-Service -Name $serviceName -ErrorAction SilentlyContinue
        }
        throw
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
        & $scCommand.Source config $serviceName 'binPath=' $serviceExe 'obj=' 'NT AUTHORITY\LocalService' 'start=' 'auto' | Out-Null
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
        $health = Invoke-AutonomousStateBroker -Method GET -Path "/v1/health/verify" -TimeoutMilliseconds 30000
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

Export-ModuleMember -Function Get-AutonomousStateBrokerPackage, Install-AutonomousStateBrokerService, Test-AutonomousDotNetFramework48
) {
        throw "The packaged Windows state broker SHA-256 manifest is malformed."
    }
    $packageHash = (Get-FileHash -LiteralPath $packagedExe -Algorithm SHA256 -ErrorAction Stop).Hash
    if (-not [string]::Equals($packageHash, $expectedHash, [StringComparison]::OrdinalIgnoreCase)) {
        throw "The packaged Windows state broker failed SHA-256 verification; installation was refused."
    }

    $frameworkRelease = Get-AutonomousDotNetFrameworkRelease
    if ($frameworkRelease -lt 528040) {
        throw "The Windows state broker requires the .NET Framework 4.8 runtime capability (Release >= 528040). No service or state changes were made."
    }
    $scCommand = Get-Command sc.exe -ErrorAction Stop

    return [pscustomobject]@{
        Path = $packagedExe
        Sha256 = $expectedHash.ToLowerInvariant()
        ServiceRoot = Join-Path $programData ("PersonalAIAssistant" + [char]92 + "StateBroker")
        ScCommand = $scCommand.Source
        FrameworkRelease = $frameworkRelease
    }
}

function Install-AutonomousStateBrokerService {
    $serviceName = "PersonalAIAssistantStateBroker"
    $serviceRoot = Join-Path $env:ProgramData "PersonalAIAssistant\StateBroker"
    $serviceSource = Join-Path $PSScriptRoot "AutonomousStateBrokerService.cs"
    if (-not (Test-Path -LiteralPath $serviceSource -PathType Leaf)) {
        throw "AutonomousStateBrokerService.cs is missing from the provisioning package."
    }
    $frameworkRelease = Get-AutonomousDotNetFrameworkRelease
    if ($frameworkRelease -lt 528040) {
        throw "The Windows state broker requires .NET Framework 4.8 or later (Release >= 528040). No service or state changes were made."
    }
    $scCommand = Get-Command sc.exe -ErrorAction Stop


    # The target host receives a prebuilt, CI-produced broker. Never require a
    # C# compiler, SDK, IDE, or developer toolchain during installation.
    $packageRoot = Join-Path $PSScriptRoot "bin"
    $packagedExe = Join-Path $packageRoot "PersonalAIAssistant.StateBroker.exe"
    $hashFile = $packagedExe + ".sha256"
    if (-not (Test-Path -LiteralPath $packagedExe -PathType Leaf) -or
        -not (Test-Path -LiteralPath $hashFile -PathType Leaf)) {
        throw "The prebuilt Windows state broker package or its SHA-256 manifest is missing."
    }
    $expectedHash = (Get-Content -LiteralPath $hashFile -Raw -ErrorAction Stop).Trim()
    if ($expectedHash -notmatch '^[0-9a-fA-F]{64}$') {
        throw "The packaged Windows state broker SHA-256 manifest is malformed."
    }
    $packageHash = (Get-FileHash -LiteralPath $packagedExe -Algorithm SHA256 -ErrorAction Stop).Hash
    if (-not [string]::Equals($packageHash, $expectedHash, [StringComparison]::OrdinalIgnoreCase)) {
        throw "The packaged Windows state broker failed SHA-256 verification; installation was refused."
    }
    New-Item -ItemType Directory -Path $serviceRoot -Force | Out-Null
    $serviceExe = Join-Path $serviceRoot "PersonalAIAssistant.StateBroker.exe"
    $temporaryExe = Join-Path $serviceRoot ("broker-stage-" + [guid]::NewGuid().ToString("N") + ".exe")
    try {
        Copy-Item -LiteralPath $packagedExe -Destination $temporaryExe -Force -ErrorAction Stop
        $copiedHash = (Get-FileHash -LiteralPath $temporaryExe -Algorithm SHA256 -ErrorAction Stop).Hash
        if (-not [string]::Equals($copiedHash, $expectedHash, [StringComparison]::OrdinalIgnoreCase)) {
            throw "The staged Windows state broker binary failed SHA-256 verification."
        }
    } catch {
        Remove-Item -LiteralPath $temporaryExe -Force -ErrorAction SilentlyContinue
        throw
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
        & $scCommand.Source config $serviceName 'binPath=' $serviceExe 'obj=' 'NT AUTHORITY\LocalService' 'start=' 'auto' | Out-Null
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
        $health = Invoke-AutonomousStateBroker -Method GET -Path "/v1/health/verify" -TimeoutMilliseconds 30000
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

Export-ModuleMember -Function Install-AutonomousStateBrokerService, Test-AutonomousDotNetFramework48
