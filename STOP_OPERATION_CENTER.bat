@echo off
setlocal
cd /d "%~dp0"
echo Stopping AERO SYNC portable server...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root=(Resolve-Path '.').Path; " ^
  "$lock=Join-Path $root 'data\runtime\aero_sync.lock'; " ^
  "$pids=@(); " ^
  "try { if(Test-Path $lock){ $fs=[System.IO.File]::Open($lock,'Open','ReadWrite','ReadWrite'); $sr=New-Object System.IO.StreamReader($fs); $raw=$sr.ReadToEnd().Trim(); $sr.Close(); $fs.Close(); if($raw -match '^\d+$'){ $pids += [int]$raw } } } catch {} " ^
  "$ports=@(19000,19001,19002,19003,19004,19005,19006,19007); " ^
  "$portPids=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $ports -contains $_.LocalPort } | Select-Object -ExpandProperty OwningProcess); " ^
  "$pids += $portPids; " ^
  "$procs=Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and ($_.CommandLine -like ('*'+$root+'*') -and ($_.CommandLine -match 'server.py|mosquitto|mosquitto_sub|ffmpeg')) }; " ^
  "$pids += @($procs.ProcessId); " ^
  "$pids=$pids | Sort-Object -Unique; " ^
  "foreach($id in $pids){ try { Stop-Process -Id $id -Force -ErrorAction Stop; Write-Host ('Stopped PID '+$id) } catch { Write-Host ('PID '+$id+' not running or access denied') } } " ^
  "if(Test-Path $lock){ Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue }"

echo Done.
pause
