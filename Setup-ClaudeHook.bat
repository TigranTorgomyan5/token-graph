@echo off
rem Token Graph: install the Claude Code cross-service hook, update installed skill copies, restart the Explorer.
setlocal
cd /d "%~dp0token-graph"
set "PY=%CD%\.runtime\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
echo 1/3 Installing the Claude Code cross-service hook...
"%PY%" scripts\system_links.py install-hook
if errorlevel 1 goto fail
echo 2/3 Updating installed skill copies...
set "FOUND="
for %%D in ("%USERPROFILE%\.claude\skills\token-graph" "%USERPROFILE%\.codex\skills\token-graph") do (
  if exist "%%~D\SKILL.md" (
    robocopy "%~dp0token-graph" "%%~D" /E /XD .runtime __pycache__ /XF .explorer-*.json /NFL /NDL /NJH /NJS /NP >nul
    echo    updated %%~D
    set "FOUND=1"
  )
)
if not defined FOUND echo    no installed skill copy found in .claude\skills or .codex\skills
echo 3/3 Restarting the Token Graph Explorer...
cd /d "%~dp0"
call Start-TokenGraph.bat
echo.
echo Done. You can close this window.
pause
exit /b 0
:fail
echo Setup failed. Copy the error above and send it to Claude.
pause
exit /b 1
