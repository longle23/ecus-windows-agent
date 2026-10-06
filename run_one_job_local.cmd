@echo off
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" scripts\run_one_job_local.py %*
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\run_one_job_local.py %*
) else (
  python scripts\run_one_job_local.py %*
)
exit /b %ERRORLEVEL%
