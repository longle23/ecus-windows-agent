# Test POST /jobs against a running local agent, then poll until it finishes.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$apiKey = "CHANGE_ME"
if (Test-Path ".env") {
  Get-Content ".env" | ForEach-Object {
    if ($_ -match '^\s*API_KEY\s*=\s*(.+)\s*$') { $apiKey = $Matches[1].Trim() }
  }
}

function Invoke-Agent($Method, $Uri, $BodyBytes) {
  $params = @{
    Method          = $Method
    Uri             = $Uri
    Headers         = @{ "X-Api-Key" = $apiKey }
    UseBasicParsing = $true
  }
  if ($BodyBytes) {
    $params.ContentType = "application/json; charset=utf-8"
    $params.Body = $BodyBytes
  }
  $response = Invoke-WebRequest @params
  $text = [System.Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray())
  Write-Host $text
  return $text | ConvertFrom-Json
}

$bytes = [System.IO.File]::ReadAllBytes((Resolve-Path ".\samples\job.sample.json"))
Write-Host "POST http://127.0.0.1:8787/jobs ..."
$accepted = Invoke-Agent Post "http://127.0.0.1:8787/jobs" $bytes
if ($accepted.status -in @("success", "failed")) {
  if (-not $accepted.success) { exit 1 }
  exit 0
}

$deadline = (Get-Date).AddMinutes(30)
do {
  Start-Sleep -Seconds 2
  $job = Invoke-Agent Get "http://127.0.0.1:8787/jobs/$($accepted.jobId)" $null
  Write-Host ("status={0} position={1}" -f $job.status, $job.position)
} while ($job.status -notin @("success", "failed") -and (Get-Date) -lt $deadline)

if ($job.status -notin @("success", "failed")) {
  Write-Host "job $($accepted.jobId) still $($job.status) after 30 minutes"
  exit 1
}
if (-not $job.success) { exit 1 }
