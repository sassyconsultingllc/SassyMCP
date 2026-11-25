# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-KTFI343JRDXL
# Check if SASSYMCP_AUTH_TOKEN is set at User scope
if ([Environment]::GetEnvironmentVariable("SASSYMCP_AUTH_TOKEN", "User")) {
    exit 0
} else {
    exit 1
}
