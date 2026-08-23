@echo off
setlocal
cd /d "%~dp0"
REM v1.0.7: detach the Watchdog from CMD/Windows Terminal.
REM The VBS launcher starts PowerShell hidden and returns immediately.
start "" /b wscript.exe "%~dp0tools\watchdog\LaunchAeroSyncWatchdog.vbs"
exit /b 0
