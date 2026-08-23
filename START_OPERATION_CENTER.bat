@echo off
setlocal
cd /d "%~dp0"
title AERO SYNC

echo =========================================
echo AERO SYNC - Portable Test
echo AERO SYNC ^| Developed by AERO NEX ^| Contact us : Support@aeronex.ae
echo =========================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python is not available in PATH.
  echo For this portable test build, install Python 3.10+ or use the final installer.
  pause
  exit /b 1
)

python app\server.py
pause
