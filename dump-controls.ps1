# Dump ECUS UI tree (no Inspect.exe needed).
# 1) Open ECUS → Tờ khai hải quan → Đăng ký mới tờ khai xuất khẩu (EDA)
# 2) Run this script
# 3) Send samples\ecus-controls-dump.txt

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
  Write-Host "Creating .venv and installing deps..."
  py -3 -m venv .venv
  if ($LASTEXITCODE -ne 0) { python -m venv .venv }
  & .\.venv\Scripts\pip.exe install -r requirements.txt
}

& .\.venv\Scripts\python.exe .\dump_controls.py
if ($LASTEXITCODE -eq 0) {
  Write-Host ""
  Write-Host "OK -> samples\ecus-controls-dump.txt"
}
exit $LASTEXITCODE
