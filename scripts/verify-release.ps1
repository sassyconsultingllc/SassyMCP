# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-CAJU3HNNRRDV
# Runs the verification commands declared in a release's notes.
#
# Convention: docs/releases/vX.Y.Z.md may contain a section like
#
#   ## Verification
#
#   ```ps1
#   python tests/test_foo.py
#   python tests/test_bar.py
#   ```
#
# This script parses that block and executes each non-comment line, then
# reports an aggregate pass/fail and exits non-zero if any line failed.
#
# Usage:
#   scripts/verify-release.ps1                  # current __version__
#   scripts/verify-release.ps1 -Version 1.4.2
#   scripts/verify-release.ps1 -All             # run every tests/test_*.py
[CmdletBinding()]
param(
    [string]$Version,
    [switch]$All
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

if (-not $Version) {
    $initText = Get-Content (Join-Path $repo 'sassymcp/__init__.py') -Raw
    if ($initText -match '__version__\s*=\s*"([^"]+)"') {
        $Version = $matches[1]
    } else {
        throw "Could not parse __version__ from sassymcp/__init__.py"
    }
}

$notesPath = Join-Path $repo "docs/releases/v$Version.md"
Write-Host "Verifying release v$Version" -ForegroundColor Cyan
if (Test-Path $notesPath) {
    Write-Host "Notes: $notesPath"
} else {
    Write-Host "Notes: (none at $notesPath)"
}
Write-Host ""

$commands = @()
if ((Test-Path $notesPath) -and -not $All) {
    $text = Get-Content $notesPath -Raw
    # Match a fenced block under a "Verification" heading (## or ###). The
    # fence language can be ps1, powershell, pwsh, or omitted.
    $pattern = '(?ms)#{2,3}\s*Verification\b.*?```(?:ps1|powershell|pwsh)?\s*\r?\n(.*?)\r?\n```'
    if ($text -match $pattern) {
        $commands = $matches[1] -split "`r?`n" |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ -and -not $_.StartsWith('#') }
    } else {
        Write-Warning "No '## Verification' fenced block found in $notesPath"
    }
}

if (-not $commands -or $All) {
    Write-Host "Falling back: running every tests/test_*.py" -ForegroundColor Yellow
    $commands = Get-ChildItem (Join-Path $repo 'tests') -Filter 'test_*.py' |
        ForEach-Object { "python `"$($_.FullName)`"" }
}

if (-not $commands) {
    Write-Warning "No verification commands to run."
    exit 0
}

$results = [System.Collections.Generic.List[object]]::new()
foreach ($cmd in $commands) {
    Write-Host ""
    Write-Host "==> $cmd" -ForegroundColor Cyan
    & pwsh -NoProfile -Command $cmd
    $code = $LASTEXITCODE
    $results.Add([pscustomobject]@{ cmd = $cmd; exit = $code; ok = ($code -eq 0) })
    if ($code -eq 0) {
        Write-Host "    OK" -ForegroundColor Green
    } else {
        Write-Host "    FAIL (exit $code)" -ForegroundColor Red
    }
}

$failed = $results | Where-Object { -not $_.ok }
Write-Host ""
Write-Host ("Verification summary for v{0}:  {1} ok, {2} failed" -f $Version, ($results.Count - $failed.Count), $failed.Count)
if ($failed) {
    $failed | ForEach-Object { Write-Host "  - $($_.cmd)  [exit $($_.exit)]" -ForegroundColor Red }
    exit 1
}
exit 0
