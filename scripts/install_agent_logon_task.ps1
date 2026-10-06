# Register a logon task that runs the desktop-session agent supervisor.
$ErrorActionPreference = "Stop"
$watcher = Join-Path $PSScriptRoot "watch_agent.ps1"
if (-not (Test-Path $watcher)) {
    throw "Missing $watcher"
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$watcher`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "ECUS Windows Agent" -Action $action -Trigger $trigger -Settings $settings -User $env:USERNAME -RunLevel Limited -Force | Out-Null
Write-Host "Registered logon task 'ECUS Windows Agent' for $env:USERNAME"
