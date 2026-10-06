@echo off
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" scripts\dump_ecus_ui.py %*
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\dump_ecus_ui.py %*
) else (
  python scripts\dump_ecus_ui.py %*
)
exit /b %ERRORLEVEL%
