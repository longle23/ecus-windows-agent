# ECUS Windows Agent
#
# 1) Copy .env.example -> .env and edit API_KEY / ECUS_EXE_PATH
# 2) Run this script (creates .venv on first run)
# 3) Point n8n Normalize ECUS Jobs AGENT_URL to http://<this-pc>:8787/jobs

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
  Write-Host "Creating .venv ..."
  py -3 -m venv .venv
  if ($LASTEXITCODE -ne 0) {
    python -m venv .venv
  }
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\pip.exe install -r requirements.txt

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Created .env from .env.example - edit API_KEY before production use."
}

Write-Host "Starting ECUS Windows Agent on http://0.0.0.0:8787 ..."
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8787
