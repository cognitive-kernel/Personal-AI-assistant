$ErrorActionPreference = "Stop"
$model = if ($env:AUTONOMOUS_ENGINEERING_MODEL) { $env:AUTONOMOUS_ENGINEERING_MODEL } else { "gpt-oss:20b" }
$checks = @()

$codexCommand = Get-Command codex -ErrorAction SilentlyContinue
$codexReady = [bool]$codexCommand
$codexVersion = "Codex CLI not found in Runner account PATH"
if ($codexReady) {
    $codexVersion = (& codex --version 2>$null | Select-Object -First 1)
    $codexReady = ($LASTEXITCODE -eq 0 -and [bool]$codexVersion)
}
$checks += [pscustomobject]@{ Component = "Codex CLI"; Ready = $codexReady; Detail = $codexVersion }

$ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
$ollamaCliReady = [bool]$ollamaCommand
$ollamaVersion = "Ollama CLI not found in Runner account PATH"
if ($ollamaCliReady) {
    $ollamaVersion = (& ollama --version 2>$null | Select-Object -First 1)
    $ollamaCliReady = ($LASTEXITCODE -eq 0 -and [bool]$ollamaVersion)
}
$ollamaOnline = $false
$modelInstalled = $false
try {
    $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 4
    $ollamaOnline = $true
    $modelInstalled = @($tags.models | ForEach-Object { $_.name }) -contains $model
} catch {}
$checks += [pscustomobject]@{ Component = "Ollama CLI"; Ready = $ollamaCliReady; Detail = if ($ollamaCliReady) { $ollamaVersion } else { "Install Ollama CLI or add it to the Runner service PATH" } }
$checks += [pscustomobject]@{ Component = "Ollama server"; Ready = $ollamaOnline; Detail = if ($ollamaOnline) { "127.0.0.1:11434" } else { "Local Ollama endpoint is unavailable" } }
$checks += [pscustomobject]@{ Component = "Configured model"; Ready = $modelInstalled; Detail = if ($modelInstalled) { $model } else { "Install the configured model in Ollama: $model" } }

$runner = Get-Process -Name "Runner.Listener" -ErrorAction SilentlyContinue | Select-Object -First 1
$runnerReady = [bool]$runner
$checks += [pscustomobject]@{ Component = "GitHub Runner"; Ready = $runnerReady; Detail = if ($runnerReady) { "Listener process active" } else { "Runner.Listener is not running in this account" } }

$progressPath = Join-Path $env:ProgramData "PersonalAIAssistant\autonomous-engineering"
$progressReady = $false
$probe = Join-Path $progressPath ".runner-write-check-$PID.tmp"
try {
    if (Test-Path -LiteralPath $progressPath -PathType Container) {
        [System.IO.File]::WriteAllText($probe, "ok")
        Remove-Item -LiteralPath $probe -Force
        $progressReady = $true
    }
} catch {
    if (Test-Path -LiteralPath $probe) {
        Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
    }
}
$checks += [pscustomobject]@{ Component = "Shared progress storage"; Ready = $progressReady; Detail = if ($progressReady) { "Runner account can write to $progressPath" } else { "Run configure_autonomous_engineering_progress.ps1 with the exact Runner service account" } }

$checks | Format-Table -AutoSize
if (@($checks | Where-Object { -not $_.Ready }).Count -gt 0) {
    exit 1
}
Write-Host "Runner-account prerequisites are ready. No GitHub CLI login is required in this account."
