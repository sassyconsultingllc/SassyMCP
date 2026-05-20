# Pings local bridge (and optionally a remote tunnel), prints HTTP status.
#
# Usage:
#   .\smoke-test.ps1                                          # local only
#   .\smoke-test.ps1 -RemoteUrl https://mcp.<your>.tld/mcp     # both
param(
    [string]$LocalUrl  = "http://127.0.0.1:21001/mcp",
    [string]$RemoteUrl = ""
)

$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$authHeader = & (Join-Path $dir "get-auth-header.ps1")
if (-not $authHeader) { Write-Host "  [SKIP] no token"; exit 0 }

$body = & (Join-Path $dir "mcp-init-body.ps1")

$headers = @{
    "Authorization" = $authHeader
    "Content-Type"  = "application/json"
    "Accept"        = "application/json, text/event-stream"
}

function Ping($label, $url, $timeout) {
    try {
        $r = Invoke-WebRequest -Uri $url -Method POST -Headers $headers -Body $body -UseBasicParsing -TimeoutSec $timeout
        Write-Host ("  [{0}] HTTP {1}" -f $label, $r.StatusCode)
    } catch {
        Write-Host ("  [{0}] {1}" -f $label, $_.Exception.Message)
    }
}

Ping "local "  $LocalUrl  5
if ($RemoteUrl) {
    Ping "remote" $RemoteUrl 10
} else {
    Write-Host "  [remote] (skipped — pass -RemoteUrl to test your tunnel)"
}
