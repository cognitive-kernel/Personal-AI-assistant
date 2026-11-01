param([string]$SelectedModel)
$ErrorActionPreference = "Stop"
$model = if (-not [string]::IsNullOrWhiteSpace($SelectedModel)) {
    $SelectedModel
} elseif ($env:AUTONOMOUS_ENGINEERING_MODEL) {
    $env:AUTONOMOUS_ENGINEERING_MODEL
} else {
    "gpt-oss:20b"
}
$checks = @()

$ollamaOnline = $false
$modelInstalled = $false
try {
    $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 4
    $ollamaOnline = $true
    $modelInstalled = @($tags.models | ForEach-Object { $_.name }) -contains $model
} catch {}
$checks += [pscustomobject]@{ Component = "Ollama server"; Ready = $ollamaOnline; Detail = if ($ollamaOnline) { "127.0.0.1:11434" } else { "Start the local Ollama service" } }
$checks += [pscustomobject]@{ Component = "Local model"; Ready = $modelInstalled; Detail = if ($modelInstalled) { $model } else { "Install the configured model in Ollama" } }

$ghCommand = Get-Command gh -ErrorAction SilentlyContinue
$ghReady = $false
if ($ghCommand) {
    gh auth status --hostname github.com *> $null
    $ghReady = ($LASTEXITCODE -eq 0)
}
$checks += [pscustomobject]@{ Component = "GitHub CLI"; Ready = $ghReady; Detail = if ($ghReady) { "Authenticated" } else { "Install gh and run gh auth login" } }

$runner = Get-Process -Name "Runner.Listener" -ErrorAction SilentlyContinue | Select-Object -First 1
$runnerReady = [bool]$runner
$checks += [pscustomobject]@{ Component = "GitHub Runner"; Ready = $runnerReady; Detail = if ($runnerReady) { "Listener process active" } else { "Register and start the self-hosted runner with label ollama" } }

$progressPath = Join-Path $env:ProgramData "PersonalAIAssistant\autonomous-engineering"
$progressReady = $false
if (Test-Path -LiteralPath $progressPath -PathType Container) {
    try {
        Get-ChildItem -LiteralPath $progressPath -ErrorAction Stop | Select-Object -First 1 | Out-Null
        $progressReady = $true
    } catch {}
}
$checks += [pscustomobject]@{
    Component = "Shared progress storage"
    Ready = $progressReady
    Detail = if ($progressReady) { $progressPath } else { "Run configure_autonomous_engineering_progress.ps1 for the app and runner accounts" }
}

$checks | Format-Table -AutoSize
if (@($checks | Where-Object { -not $_.Ready }).Count -gt 0) {
    exit 1
}
Write-Host "App-account prerequisites are ready. Run check_autonomous_runner.ps1 as the dedicated Runner service account to verify Codex and runner access."
