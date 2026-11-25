# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-WBVO5DVBZQ6N
# Build the MCP auth header value from env var
$t = [Environment]::GetEnvironmentVariable("SASSYMCP_AUTH_TOKEN", "User")
if (-not $t) { $t = $env:SASSYMCP_AUTH_TOKEN }
if (-not $t) { return $null }
return ("Bearer " + $t)
