# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-DGZAX3DSBVCY
# Build sassymcp.mcpb from the PyInstaller exe + mcpb/manifest.json.
#
# Produces an Anthropic MCP Bundle (.mcpb, the format that replaced .dxt)
# using the official @anthropic-ai/mcpb CLI so the manifest is validated
# and the zip entry names are spec-correct — no hand-rolled archive.
#
# Prereqs:
#   - node/npx on PATH (the CLI is fetched via npx).
#   - sassymcp.exe built (run build.bat first, or this script will).
#   - mcpb/manifest.json present, manifest_version "0.2", version matching
#     sassymcp/__init__.py __version__.
#
# Output: dist/sassymcp-v<version>.mcpb
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$exeDist = Join-Path $root "dist\sassymcp.exe"   # PyInstaller's actual output
$exeRoot = Join-Path $root "sassymcp.exe"        # legacy copy at repo root
$manifestSrc = Join-Path $root "mcpb\manifest.json"
$iconSrc = Join-Path $root "mcpb\icon.png"
$readmeSrc = Join-Path $root "mcpb\README.md"

# Prefer the freshly built exe in dist\; fall back to the legacy root copy.
# build.bat emits dist\sassymcp.exe, so never trust a stale root exe to mean
# "already built" — that silently packages an old binary.
if (Test-Path $exeDist) {
    $exe = $exeDist
} elseif (Test-Path $exeRoot) {
    $exe = $exeRoot
} else {
    Write-Host "sassymcp.exe not found, running build.bat..."
    & (Join-Path $root "build.bat")
    if (-not (Test-Path $exeDist)) {
        Write-Error "build.bat did not produce dist\sassymcp.exe"
        exit 1
    }
    $exe = $exeDist
}
Write-Host "Using exe: $exe"

# Read version from sassymcp/__init__.py (single source of truth).
$initContent = Get-Content (Join-Path $root "sassymcp\__init__.py") -Raw
if ($initContent -match '__version__\s*=\s*"([^"]+)"') {
    $version = $Matches[1]
} else {
    Write-Error "Could not parse __version__ from sassymcp/__init__.py"
    exit 1
}
Write-Host "Building MCPB for sassymcp v$version..."

# Stage to a tempdir: manifest.json + icon/README at root, exe under server/.
$staging = Join-Path $env:TEMP "sassymcp-mcpb-staging-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $staging | Out-Null
try {
    New-Item -ItemType Directory -Path (Join-Path $staging "server") | Out-Null
    Copy-Item $exe (Join-Path $staging "server\sassymcp.exe")
    Copy-Item $manifestSrc (Join-Path $staging "manifest.json")
    if (Test-Path $iconSrc) { Copy-Item $iconSrc (Join-Path $staging "icon.png") }
    if (Test-Path $readmeSrc) { Copy-Item $readmeSrc (Join-Path $staging "README.md") }

    # Keep the manifest version in lockstep with __version__.
    $manifestObj = Get-Content (Join-Path $staging "manifest.json") -Raw | ConvertFrom-Json
    if ($manifestObj.version -ne $version) {
        Write-Warning "manifest version $($manifestObj.version) != __version__ $version. Updating staged copy."
        $manifestObj.version = $version
        $manifestObj | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $staging "manifest.json") -Encoding UTF8
    }

    $distDir = Join-Path $root "dist"
    New-Item -ItemType Directory -Path $distDir -Force | Out-Null
    $output = Join-Path $distDir "sassymcp-v$version.mcpb"
    if (Test-Path $output) { Remove-Item $output -Force }

    # Validate the manifest, then pack with the official CLI. `pack` writes
    # forward-slash zip entries and enforces the manifest schema, so the
    # bundle loads in Claude Desktop without the slash/entry_point pitfalls
    # the old hand-rolled .dxt zip had to work around.
    & npx -y "@anthropic-ai/mcpb@latest" validate (Join-Path $staging "manifest.json")
    if ($LASTEXITCODE -ne 0) { Write-Error "mcpb validate failed"; exit 1 }

    & npx -y "@anthropic-ai/mcpb@latest" pack $staging $output
    if ($LASTEXITCODE -ne 0) { Write-Error "mcpb pack failed"; exit 1 }

    & npx -y "@anthropic-ai/mcpb@latest" info $output

    Write-Host "Built: $output ($([math]::Round((Get-Item $output).Length / 1MB, 1)) MB)"

    # Future-/back-compat: an .mcpb IS a .dxt — identical zip + manifest
    # format; Anthropic only renamed the extension. Older Claude Desktop
    # builds (and any client still keyed to the old name) won't recognize
    # .mcpb but will install the exact same bytes as .dxt. Emit both so a
    # single build covers every client, old and new. Attach both to the
    # GitHub release.
    $dxt = Join-Path $distDir "sassymcp-v$version.dxt"
    Copy-Item $output $dxt -Force
    Write-Host "Also emitted (compat): $dxt"
} finally {
    Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
}
