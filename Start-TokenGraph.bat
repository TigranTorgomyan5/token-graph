@echo off
rem Token Graph: installs the parser runtime on first run, then opens the Project Explorer.
setlocal
cd /d "%~dp0token-graph"

where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3.11+ from python.org and tick "Add Python to PATH".
  pause
  exit /b 1
)

if not exist ".runtime\Scripts\python.exe" goto setup
".runtime\Scripts\python.exe" -I -c "import networkx, numpy, rapidfuzz, tree_sitter, tree_sitter_c_sharp" >nul 2>nul
if errorlevel 1 goto setup
goto start

:setup
echo Installing Token Graph parsers (one time, about 150 MB)...
python scripts\setup_runtime.py --download-deps --installer pip
if errorlevel 1 (
  echo.
  echo Setup failed. Copy the error above and send it to Claude.
  pause
  exit /b 1
)

:start
echo Starting Token Graph Project Explorer...
start "" ".runtime\Scripts\pythonw.exe" scripts\explorer_server.py
endlocal
