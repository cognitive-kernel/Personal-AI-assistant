@echo off
setlocal
set "_SCRIPT_DIR=%~dp0"
set "_BOOTSTRAP=%_SCRIPT_DIR%bootstrap.ps1"

where.exe pwsh.exe >nul 2>nul
if not errorlevel 1 (
    set "_POWERSHELL=pwsh.exe"
) else (
    where.exe powershell.exe >nul 2>nul
    if errorlevel 1 (
        echo PowerShell was not found. Windows PowerShell 5.1 or PowerShell 7 is required.
        exit /b 2
    )
    set "_POWERSHELL=powershell.exe"
)

"%_POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%_BOOTSTRAP%" %*
set "_EXIT_CODE=%ERRORLEVEL%"
exit /b %_EXIT_CODE%
