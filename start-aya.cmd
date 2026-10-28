@echo off
title Aya launcher
cd /d "%~dp0"

REM ============================================================
REM  Aya launcher: core (silent, pythonw) + window via tauri dev.
REM  Re-running this script is safe: live parts are skipped.
REM ============================================================

call :port_busy 8330
if %ERRORLEVEL% EQU 0 (
  echo [core] already running - skipping.
) else (
  if not exist logs mkdir logs
  for %%F in (core.out core.err) do (
    if exist logs\%%F (
      if exist logs\%%F.1 del logs\%%F.1
      ren logs\%%F %%F.1 >nul
    )
  )
  echo [core] starting...
  start "Aya core" /min cmd /c "core\.venv\Scripts\pythonw.exe core\main.py 1>>logs\core.out 2>>logs\core.err"
)

echo [window] starting tauri dev (you can minimize this terminal)...
call npm run tauri dev
goto :eof

:port_busy
netstat -ano | findstr /R /C:":%1 .*LISTENING" >nul 2>&1
exit /b %ERRORLEVEL%
