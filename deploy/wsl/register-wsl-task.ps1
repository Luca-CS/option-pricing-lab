# A Windows scheduled task that starts WSL at Luca's logon and keeps it running, so the containers of
# deploy/compose.yaml run 24/7 (ADR-0009: WSL stops its VM a few minutes after the last wsl.exe session, and nothing
# starts it after a reboot). A Windows scheduled task: run it only with Luca's agreement (handoff §0; given on
# 13 November 2026).
#
#     powershell -ExecutionPolicy Bypass -File deploy\wsl\register-wsl-task.ps1     # no administrator needed; re-run to update
#
# What it runs, as Luca, at his logon, without a window (conhost --headless): a loop that holds a `wsl.exe -d Ubuntu
# --exec /bin/sleep infinity` session and starts it again 30 s after it ends (a `wsl --shutdown`, a WSL update). The
# containers start with the distribution (systemd, Docker, restart: unless-stopped); `docker compose down` stops
# them for good, the task only keeps WSL up.
#
# Undo: Unregister-ScheduledTask -TaskName "optlab WSL" -Confirm:$false   (then stop the loop: log off, or end the
#       "conhost.exe" running powershell in the Task Manager)
$ErrorActionPreference = "Stop"
$name = "optlab WSL"
$loop = 'while ($true) { & "$env:SystemRoot\System32\wsl.exe" -d Ubuntu --exec /bin/sleep infinity; Start-Sleep -Seconds 30 }'
$powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\conhost.exe" `
    -Argument "--headless `"$powershell`" -NoProfile -NonInteractive -Command `"$loop`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $name
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
}
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Starts WSL at logon and keeps it running for optlab's containers (deploy/wsl/register-wsl-task.ps1)" | Out-Null
Start-ScheduledTask -TaskName $name
Write-Output "registered and started: $name (undo: Unregister-ScheduledTask -TaskName '$name' -Confirm:`$false)"
