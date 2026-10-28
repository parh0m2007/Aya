@echo off
title Aya stop
cd /d "%~dp0"
for %%P in (8330 5173) do for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:":%%P .*LISTENING"') do taskkill /PID %%a /F >nul 2>&1
taskkill /IM aya.exe /F >nul 2>&1
echo Aya остановлена.
