$ErrorActionPreference = "Stop"
$model = if ($env:AUTONOMOUS_ENGINEERING_MODEL) { $env:AUTONOMOUS_ENGINEERING_MODEL } else { "gpt-oss:20b" }
$checks = @()

$ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
$ollamaOnline = $false
$modelInstalled = $false
if ($ollamaCommand) {
    try {
        $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 4
        $ollamaOnline = $true
        $modelInstalled = @($tags.models | ForEach-Object { $_.name }) -contains $model
    } catch {}
}
$checks += [pscustomobject]@{ Component = "Ollama server"; Ready = $ollamaOnline; Detail = if ($ollamaOnline) { "127.0.0.1:11434" } else { "Install/start Ollama" } }
$checks += [pscustomobject]@{ Component = "Local model"; Ready = $modelInstalled; Detail = if ($modelInstalled) { $model } else { "Run: ollama pull $model" } }

$codexCommand = Get-Command codex -ErrorAction SilentlyContinue
$codexReady = [bool]$codexCommand
$codexVersion = if ($codexReady) { (& codex --version 2>$null | Select-Object -First 1) } else { "Codex CLI not found" }
$checks += [pscustomobject]@{ Component = "Codex CLI"; Ready = $codexReady; Detail = $codexVersion }

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
Write-Host "All local autonomous-engineering prerequisites are ready."
