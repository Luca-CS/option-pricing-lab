<#
Opens the desk to the LAN on this machine, from a PowerShell opened as administrator (docs/architecture/operations.md,
"Open to the LAN"; ADR-0002): a port forward from the machine's LAN address to WSL's, and a Windows firewall rule for
that port, on the Private profile and for the local subnet only. -Undo removes both. The proxy itself (Caddy, the `lan`
compose profile) is started by the OPTLAB_LAN settings of deploy/.env.

    powershell -ExecutionPolicy Bypass -File deploy\wsl\open-lan.ps1              # open (the network must be Private)
    powershell -ExecutionPolicy Bypass -File deploy\wsl\open-lan.ps1 -SetPrivate  # on your home network: make it Private
    powershell -ExecutionPolicy Bypass -File deploy\wsl\open-lan.ps1 -Undo        # close

WSL's address changes when WSL restarts, and the LAN address when the router gives another one: run it again then (it
replaces its forward), and put a new LAN address in deploy/.env (OPTLAB_LAN_NAMES, OPTLAB_LAN_SNI).
#>
param([int]$Port = 8443, [switch]$SetPrivate, [switch]$Undo)
$ErrorActionPreference = "Stop"

$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "run it from a PowerShell opened as administrator"
}
$rule = "optlab LAN $Port"
$route = Get-NetRoute -DestinationPrefix 0.0.0.0/0 | Sort-Object RouteMetric | Select-Object -First 1
$lan = (Get-NetIPAddress -InterfaceIndex $route.ifIndex -AddressFamily IPv4 | Select-Object -First 1).IPAddress

if ($Undo) {
    netsh interface portproxy delete v4tov4 listenaddress=$lan listenport=$Port 2>$null | Out-Null
    Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    "closed: no forward on ${lan}:$Port, no rule '$rule'"
    return
}

$network = Get-NetConnectionProfile -InterfaceIndex $route.ifIndex
if ($network.NetworkCategory -ne "Private") {
    if (-not $SetPrivate) {
        throw "the network '$($network.Name)' is $($network.NetworkCategory): the rule holds on a Private network only. On your home network, run it again with -SetPrivate"
    }
    Set-NetConnectionProfile -InterfaceIndex $route.ifIndex -NetworkCategory Private
}

$wsl = ((wsl -d Ubuntu -u root -- hostname -I) -split " ")[0].Trim()
netsh interface portproxy delete v4tov4 listenaddress=$lan listenport=$Port 2>$null | Out-Null
netsh interface portproxy add v4tov4 listenaddress=$lan listenport=$Port connectaddress=$wsl connectport=$Port | Out-Null
Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $rule -Direction Inbound -Protocol TCP -LocalPort $Port -Profile Private `
    -RemoteAddress LocalSubnet -Action Allow | Out-Null
"open: https://${lan}:$Port -> WSL ${wsl}:$Port; firewall rule '$rule' (Private profile, local subnet)"
