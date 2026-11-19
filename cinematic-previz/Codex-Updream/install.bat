@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Updream for Codex - Automatic Installer
echo ============================================
echo   Updream for Codex - Automatic Installer
echo ============================================
echo No Edge extension or JSON pairing is required.
echo.
if not exist "%~dp0runtime\node.exe" goto MISSING
"%~dp0runtime\node.exe" "%~dp0scripts\install.cjs"
if errorlevel 1 goto FAILED
goto END
:MISSING
echo [ERROR] Extract the complete Windows ZIP before running this file.
goto END
:FAILED
echo [ERROR] Installation did not finish. Read the message above.
:END
echo.
pause
