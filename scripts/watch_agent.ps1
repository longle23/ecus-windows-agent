# Keep the ECUS agent running in the interactive desktop session.
# A Windows service cannot drive ECUS UI (Session 0), so this watcher
# starts uvicorn only when the logged-on user session has no healthy agent.
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root ".venv\Scripts\python.exe"
$logDir = Join-Path $root "logs"
$logFile = Join-Path $logDir "supervisor.log"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, "Local\ECUSWindowsAgentSupervisor", [ref]$createdNew)
if (-not $createdNew) {
    exit 0
}

function Write-SupervisorLog([string]$message) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $message
    Add-Content -Path $logFile -Value $line -Encoding utf8
}

function Test-AgentHealthy {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:8787/health" -UseBasicParsing -TimeoutSec 5
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Get-AgentProcess {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -like "*uvicorn*app.main:app*" }
}

Write-SupervisorLog "supervisor started"
try {
    while ($true) {
        if (Test-AgentHealthy) {
            Start-Sleep -Seconds 15
            continue
        }
        $running = @(Get-AgentProcess)
        if ($running.Count -gt 0) {
            Write-SupervisorLog "health check failed but uvicorn process is still alive; not starting another"
            Start-Sleep -Seconds 15
            continue
        }
        if (-not (Test-Path $python)) {
            Write-SupervisorLog "python not found at $python"
            Start-Sleep -Seconds 30
            continue
        }
        Write-SupervisorLog "starting uvicorn"
        Start-Process -FilePath $python -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787") -WorkingDirectory $root -WindowStyle Minimized
        Start-Sleep -Seconds 10
    }
} finally {
    $mutex.ReleaseMutex() | Out-Null
    $mutex.Dispose()
}
