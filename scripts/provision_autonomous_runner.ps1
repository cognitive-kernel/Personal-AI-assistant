[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")][string]$Repository,
    [Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$RunnerAccount,
    [Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$AppAccount,
    [string]$RunnerName = "$env:COMPUTERNAME-ollama",
    [string]$RunnerRoot = "C:\actions-runner",
    [string]$Model = ""
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Run from elevated PowerShell." }
$RunnerAccount = $RunnerAccount.Trim()
$AppAccount = $AppAccount.Trim()
if ($RunnerAccount -notmatch "^[A-Za-z0-9_.-]{1,20}$" -or $AppAccount -notmatch "^[A-Za-z0-9_.-]{1,20}$" -or $RunnerAccount -ieq $AppAccount) { throw "Use two different local account names containing only letters, digits, dot, underscore, or hyphen (max 20 characters)." }
if ($RunnerName -notmatch "^[A-Za-z0-9_.-]{1,64}$") { throw "Invalid RunnerName." }
$runnerIdentity = "$env:COMPUTERNAME\$RunnerAccount"
$appIdentity = "$env:COMPUTERNAME\$AppAccount"
function Get-AccountSidValue([string]$AccountName) {
    try {
        return ([Security.Principal.NTAccount]$AccountName).Translate([Security.Principal.SecurityIdentifier]).Value
    } catch {
        throw "Could not resolve Windows account '$AccountName' to a SID."
    }
}
$progressPath = Join-Path $env:ProgramData "PersonalAIAssistant\autonomous-engineering"
try { $null = ([Security.Principal.NTAccount]$appIdentity).Translate([Security.Principal.SecurityIdentifier]) } catch { throw "Application account $appIdentity does not exist." }
$ollamaCommand = Get-Command ollama.exe -ErrorAction SilentlyContinue
$ollamaPath = if ($ollamaCommand) { $ollamaCommand.Source } else { $null }
if (-not $ollamaPath) {
    $ollamaProcess = Get-CimInstance Win32_Process -Filter "Name='ollama.exe'" -ErrorAction Stop |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_.ExecutablePath) } |
        Select-Object -First 1
    $ollamaPath = if ($ollamaProcess) { [string]$ollamaProcess.ExecutablePath } else { $null }
}
if (-not $ollamaPath) {
    $profiles = @(Get-CimInstance Win32_UserProfile -ErrorAction Stop | Where-Object {
        -not $_.Special -and -not [string]::IsNullOrWhiteSpace($_.LocalPath)
    })
    foreach ($profile in $profiles) {
        $candidate = Join-Path ([string]$profile.LocalPath) "AppData\Local\Programs\Ollama\ollama.exe"
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $ollamaPath = $candidate
            break
        }
    }
}
if ([string]::IsNullOrWhiteSpace($ollamaPath) -or -not (Test-Path -LiteralPath $ollamaPath -PathType Leaf)) {
    throw "Ollama executable was not found in PATH or among active ollama.exe processes."
}
$node = Get-Command node.exe -ErrorAction Stop
$npm = Get-Command npm.cmd -ErrorAction Stop
$git = Get-Command git.exe -ErrorAction Stop
$python = Get-Command python.exe -ErrorAction Stop
$gh = Get-Command gh.exe -ErrorAction Stop
$nodeVersion = (@(& $node.Source --version 2>&1) -join " ").Trim()
$nodeVersionNumber = $null
try { $nodeVersionNumber = [version]($nodeVersion.TrimStart("v")) } catch {}
if ($LASTEXITCODE -ne 0 -or $null -eq $nodeVersionNumber -or $nodeVersionNumber -lt [version]"26.13.0") { throw "Node.js 26.13.0 or newer is required; found '$nodeVersion'." }
$npmVersion = (@(& $npm.Source --version 2>&1) -join " ").Trim()
$npmVersionNumber = $null
try { $npmVersionNumber = [version]$npmVersion } catch {}
if ($LASTEXITCODE -ne 0 -or $null -eq $npmVersionNumber -or $npmVersionNumber -lt [version]"11.20.0") { throw "npm 11.20.0 or newer is required; found '$npmVersion'." }
$ollamaVersion = (@(& $ollamaPath --version 2>&1) -join " ").Trim()
$ollamaVersionMatch = [regex]::Match($ollamaVersion, '(?i)([0-9]+\.[0-9]+\.[0-9]+)')
$ollamaVersionNumber = $null
if ($ollamaVersionMatch.Success) { try { $ollamaVersionNumber = [version]$ollamaVersionMatch.Groups[1].Value } catch {} }
if ($LASTEXITCODE -ne 0 -or $null -eq $ollamaVersionNumber -or $ollamaVersionNumber -lt [version]"0.40.16") { throw "Ollama 0.40.16 or newer is required; found '$ollamaVersion'." }
& $git.Source --version
if ($LASTEXITCODE -ne 0) { throw "Git is not executable." }
$pythonVersion = (& $python.Source -c "import sys; print(sys.version_info.major, sys.version_info.minor, sep=chr(46))")
if ($LASTEXITCODE -ne 0 -or [version]$pythonVersion -lt [version]"3.11") { throw "Python 3.11 or newer is required on the Runner host." }
& $gh.Source auth status --hostname github.com
if ($LASTEXITCODE -ne 0) { throw "Authenticate gh as an account with repository Administration: write permission." }
if ([string]::IsNullOrWhiteSpace($Model)) {
    $Model = (& $gh.Source variable get AUTONOMOUS_ENGINEERING_MODEL --repo $Repository 2>$null | Out-String).Trim()
}
if ([string]::IsNullOrWhiteSpace($Model)) {
    throw "No saved AUTONOMOUS_ENGINEERING_MODEL repository variable exists. Save an installed model in the autonomous-engineering dashboard first."
}
if ($Model -match "(?i)-codex-(?:ctx[0-9]+|[0-9]+k)$") {
    throw "The saved model is an internal Codex context variant. Save the original Ollama model in the dashboard."
}
$tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 8 -MaximumRedirection 0
if (@($tags.models | ForEach-Object { $_.name }) -notcontains $Model) {
    throw "Saved model $Model is not installed. Install it with ollama pull $Model, then save it in the dashboard."
}
$modelInfo = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/show" -Method Post -ContentType "application/json" -Body (@{ model = $Model; verbose = $false } | ConvertTo-Json -Compress) -TimeoutSec 15 -MaximumRedirection 0
$architecture = [string]$modelInfo.details.family
$contextKey = $architecture + ".context_length"
$rawContext = $modelInfo.model_info.PSObject.Properties[$contextKey]
$parsedContext = 0L
if ([string]::IsNullOrWhiteSpace($architecture) -or -not $rawContext -or -not [int64]::TryParse([string]$rawContext.Value, [ref]$parsedContext) -or $parsedContext -lt 1) {
    throw "Saved model $Model must expose a positive valid context limit for Codex."
}
if (@($modelInfo.capabilities | ForEach-Object { [string]$_ }) -notcontains "tools") {
    throw "Saved model $Model does not advertise the required Ollama tools capability."
}
function Ensure-AutonomousStateOwnerLabel {
    $inventoryJson = & $gh.Source api "repos/$Repository/actions/runners?per_page=100"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($inventoryJson)) {
        throw "Could not inspect repository Runner inventory; refusing to assume local durable-state affinity."
    }
    $inventory = $inventoryJson | ConvertFrom-Json
    if ($null -eq $inventory.runners -or [int]$inventory.total_count -gt @($inventory.runners).Count) {
        throw "Runner inventory is incomplete; cannot prove that autonomous-state-owner is unique."
    }
    $matching = @($inventory.runners | Where-Object { $_.name -eq $RunnerName })
    if ($matching.Count -ne 1) {
        throw "Expected exactly one registered Runner named '$RunnerName'; found $($matching.Count)."
    }
    $runner = $matching[0]
    $owners = @($inventory.runners | Where-Object {
        @($_.labels | ForEach-Object { $_.name }) -contains "autonomous-state-owner"
    })
    if (@($owners | Where-Object { $_.id -ne $runner.id }).Count -gt 0) {
        throw "Another Runner already owns the autonomous-state-owner label; refusing ambiguous state affinity."
    }
    if (@($runner.labels | ForEach-Object { $_.name }) -notcontains "autonomous-state-owner") {
        $null = & $gh.Source api --method POST "repos/$Repository/actions/runners/$($runner.id)/labels" -f "labels[]=autonomous-state-owner"
        if ($LASTEXITCODE -ne 0) { throw "Could not assign the unique autonomous-state-owner Runner label." }
    }
    $verifiedJson = & $gh.Source api "repos/$Repository/actions/runners?per_page=100"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($verifiedJson)) {
        throw "Could not verify autonomous-state-owner Runner label assignment."
    }
    $verified = $verifiedJson | ConvertFrom-Json
    $owners = @($verified.runners | Where-Object {
        @($_.labels | ForEach-Object { $_.name }) -contains "autonomous-state-owner"
    })
    if ($owners.Count -ne 1 -or $owners[0].id -ne $runner.id) {
        throw "autonomous-state-owner must identify exactly the configured Runner and no other host."
    }
}

if (Test-Path -LiteralPath (Join-Path $RunnerRoot ".runner")) {
    $services = @(Get-CimInstance Win32_Service | Where-Object { $_.PathName -like "*$RunnerRoot*" })
    if ($services.Count -ne 1 -or (Get-AccountSidValue $services[0].StartName) -ne (Get-AccountSidValue $runnerIdentity)) { throw "Existing Runner configuration found; refusing to replace or reconfigure it." }
    Ensure-AutonomousStateOwnerLabel
    if ($services[0].State -ne "Running") { Start-Service -Name $services[0].Name }
    Write-Host "Existing Runner configuration preserved, uniquely labelled, and service verified."
    exit 0
}
if ((Test-Path -LiteralPath $RunnerRoot) -and @(Get-ChildItem -LiteralPath $RunnerRoot -Force).Count -gt 0) { throw "RunnerRoot is non-empty but unconfigured; refusing to overwrite." }
$user = Get-LocalUser -Name $RunnerAccount -ErrorAction SilentlyContinue
if (-not $user) {
    $password = Read-Host "Set password for $runnerIdentity" -AsSecureString
    if ($password.Length -lt 16) { $password.Dispose(); throw "Use at least 16 characters." }
    New-LocalUser -Name $RunnerAccount -Password $password -PasswordNeverExpires -AccountNeverExpires -Description "Non-admin Ollama GitHub Actions Runner" | Out-Null
    $password.Dispose()
}
$user = Get-LocalUser -Name $RunnerAccount
if (-not $user.Enabled) { Enable-LocalUser -Name $RunnerAccount }
$adminMembers = @(Get-LocalGroupMember -Group "Administrators" -ErrorAction Stop | ForEach-Object { $_.Name })
if ($adminMembers -contains $runnerIdentity) { throw "Runner account is a local administrator." }
$usersGroup = Get-LocalGroup -SID "S-1-5-32-545"
if (-not (Get-LocalGroupMember -Group $usersGroup.Name | Where-Object { $_.Name -ieq $runnerIdentity })) { Add-LocalGroupMember -Group $usersGroup.Name -Member $runnerIdentity }
New-Item -ItemType Directory -Path $RunnerRoot -Force | Out-Null
New-Item -ItemType Directory -Path $progressPath -Force | Out-Null
& icacls.exe $RunnerRoot /inheritance:r /grant:r "$($runnerIdentity):(OI)(CI)M" "SYSTEM:(OI)(CI)F" "BUILTIN\Administrators:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Runner directory ACL configuration failed." }
& icacls.exe $progressPath /inheritance:r /grant:r "$($runnerIdentity):(OI)(CI)M" "$($appIdentity):(OI)(CI)RX" "SYSTEM:(OI)(CI)F" "BUILTIN\Administrators:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Progress directory ACL configuration failed." }
$token = (& $gh.Source api --method POST "repos/$Repository/actions/runners/registration-token" --jq ".token")
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($token)) { throw "Could not obtain registration token; verify Administration: write permission." }
$archive = $null
try {
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/actions/runner/releases/latest" -Headers @{ Accept="application/vnd.github+json"; "X-GitHub-Api-Version"="2022-11-28" } -TimeoutSec 30
    $asset = @($release.assets | Where-Object { $_.name -match "^actions-runner-win-x64-[0-9.]+\.zip$" }) | Select-Object -First 1
    if (-not $asset -or $asset.digest -notmatch "^sha256:[0-9a-fA-F]{64}$") { throw "Latest official Runner asset has no verifiable SHA-256 digest." }
    $archive = Join-Path $env:TEMP ("actions-runner-" + [guid]::NewGuid().ToString("N") + ".zip")
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $archive -TimeoutSec 180
    $hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $asset.digest.Substring(7).ToLowerInvariant()) { throw "Runner archive digest mismatch." }
    Expand-Archive -LiteralPath $archive -DestinationPath $RunnerRoot -Force
    Remove-Item -LiteralPath $archive -Force
    $config = Join-Path $RunnerRoot "config.cmd"
    if (-not (Test-Path -LiteralPath $config)) { throw "config.cmd missing from official Runner archive." }
    $configArgs = @("--url", "https://github.com/$Repository", "--token", $token, "--name", $RunnerName, "--labels", "ollama,autonomous-state-owner", "--work", "_work", "--runasservice", "--windowslogonaccount", $runnerIdentity)
    Write-Host "Runner configuration will prompt for the service-account password; it is not passed as a process argument."
    & $config @configArgs
    if ($LASTEXITCODE -ne 0) { throw "Runner registration failed with exit code $LASTEXITCODE." }
    $services = @(Get-CimInstance Win32_Service | Where-Object { $_.PathName -like "*$RunnerRoot*" })
    if ($services.Count -ne 1) { throw "Could not locate exactly one Runner service." }
    $actualServiceSid = Get-AccountSidValue $services[0].StartName
    $expectedRunnerSid = Get-AccountSidValue $runnerIdentity
    if ($actualServiceSid -ne $expectedRunnerSid) { throw "Could not verify the Runner service identity." }
    if ($services[0].StartMode -eq "Disabled") { Set-Service -Name $services[0].Name -StartupType Automatic }
    Start-Service -Name $services[0].Name
    if ((Get-Service -Name $services[0].Name).Status -ne "Running") { throw "Runner service did not start." }
    Write-Host "Runner registered and running as $runnerIdentity; model $Model verified."
} finally {
    $token = $null
    if ($archive -and (Test-Path -LiteralPath $archive)) { Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue }
}
