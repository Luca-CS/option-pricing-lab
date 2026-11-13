# A Windows scheduled task that starts WSL at Luca's logon and keeps it running, so the containers of
# deploy/compose.yaml run 24/7 (ADR-0009: WSL stops its VM a few minutes after the last wsl.exe session, and nothing
# starts it after a reboot). A Windows scheduled task: run it only with Luca's agreement (handoff §0).
#
#     powershell -ExecutionPolicy Bypass -File deploy\wsl\register-wsl-task.ps1     # no administrator needed
#
# What it runs: `wsl.exe -d Ubuntu --exec /bin/sleep infinity`, without a window (conhost --headless), as Luca, at his
# logon; restarted if it ends. The containers start with the distribution (systemd, Docker, restart: unless-stopped).
#
# Undo: Unregister-ScheduledTask -TaskName "optlab WSL" -Confirm:$false
$ErrorActionPreference = "Stop"
$name = "optlab WSL"
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\conhost.exe" `
    -Argument "--headless $env:SystemRoot\System32\wsl.exe -d Ubuntu --exec /bin/sleep infinity"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Starts WSL at logon and keeps it running for optlab's containers (deploy/wsl/register-wsl-task.ps1)"
Start-ScheduledTask -TaskName $name
Write-Output "registered and started: $name (undo: Unregister-ScheduledTask -TaskName '$name' -Confirm:`$false)"
