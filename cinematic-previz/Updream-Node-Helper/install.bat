@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Updream MCP Bridge Installer
echo ============================================
echo   Updream MCP Bridge - Offline Installer
echo ============================================
echo.
if not exist "%~dp0runtime\node.exe" goto MISSING_RUNTIME
if not exist "%~dp0bundle-manifest.json" goto MISSING_RUNTIME
"%~dp0runtime\node.exe" "%~dp0scripts\install.cjs"
if errorlevel 1 goto FAILED
echo.
echo Local setup is ready. Follow the browser guide to load the extension.
goto END
:MISSING_RUNTIME
echo [ERROR] Extract the complete Windows installer ZIP before running install.bat.
echo Download the Windows x64 asset, not the GitHub source archive.
goto END
:FAILED
echo.
echo [ERROR] Setup did not finish. Read the message above.
echo No browser extension was installed silently.
:END
echo.
pause
