@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto try_py
python "scripts\skill_dashboard.py" --open
goto finish
:try_py
where py >nul 2>nul
if errorlevel 1 goto missing
py -3 "scripts\skill_dashboard.py" --open
goto finish
:missing
echo Python 3.10 or newer is required. Install Python and try again.
:finish
echo.
echo The viewer has stopped or an existing viewer was reused.
pause
endlocal
