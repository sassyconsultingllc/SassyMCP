# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-D6GKWPV6N2KS
# Build SassyMCP-v<version>.msi from a staging dir using WiX 3.x.
#
# Prereqs:
#   - WiX Toolset 3.x on PATH (candle.exe + light.exe).
#       winget install WiXToolset.WiXToolset
#   - sassymcp.exe + start-*.bat + tools/ + *.template.json staged in -SourceDir
#     (default: deploy/).  This script does NOT build the exe — run
#     `pyinstaller --clean --noconfirm sassymcp.spec` first and copy the
#     fresh dist/sassymcp.exe into the staging dir.
#
# Versioning:
#   Reads __version__ from sassymcp/__init__.py — single source of truth.
#
# UpgradeCode:
#   Hard-coded constant (do NOT regenerate per build). MajorUpgrade only
#   detects prior installs when UpgradeCode is stable across releases.
#
# Usage:
#   .\build-msi.ps1                      # uses deploy/ as source
#   .\build-msi.ps1 -SourceDir dist/staging
#   .\build-msi.ps1 -Version 1.5.0       # override (defaults to __init__.py)
[CmdletBinding()]
param(
    [string]$Version,
    [string]$SourceDir = "deploy",
    [string]$ProductName = "SassyMCP",
    [string]$Manufacturer = "SassyMCP Contributors",
    [string]$UpgradeCode = "fcc4abb1-f2c1-4ecf-846b-12d7e1e6d72e",
    [string]$WxsFile = "installer.wxs"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

if (-not $Version) {
    $initContent = Get-Content (Join-Path $root "sassymcp\__init__.py") -Raw
    if ($initContent -match '__version__\s*=\s*"([^"]+)"') {
        $Version = $Matches[1]
    } else {
        throw "Could not parse __version__ from sassymcp/__init__.py"
    }
}

$sourcePath = Resolve-Path (Join-Path $root $SourceDir) -ErrorAction SilentlyContinue
if (-not $sourcePath) {
    throw "SourceDir not found: $(Join-Path $root $SourceDir)"
}
$exeInSource = Join-Path $sourcePath "sassymcp.exe"
if (-not (Test-Path $exeInSource)) {
    throw "sassymcp.exe missing from $sourcePath. Build via `pyinstaller --clean --noconfirm sassymcp.spec` and copy dist\sassymcp.exe into the staging dir."
}

$OutputMsi = "SassyMCP-v$Version.msi"
Write-Host "Building $OutputMsi from $sourcePath (version $Version)..."

# Generate WXS
$wxs = @"
<?xml version="1.0" encoding="UTF-8"?>
<Wix xmlns="http://schemas.microsoft.com/wix/2006/wi">
  <Product Id="*" Name="$ProductName" Language="1033" Version="$Version" Manufacturer="$Manufacturer" UpgradeCode="$UpgradeCode">
    <Package InstallerVersion="500" Compressed="yes" InstallScope="perMachine" />
    <MajorUpgrade DowngradeErrorMessage="A newer version of $ProductName is already installed." />
    <MediaTemplate />
    <Feature Id="ProductFeature" Title="$ProductName" Level="1">
      <ComponentGroupRef Id="AppFiles" />
    </Feature>
  </Product>
  <Fragment>
    <Directory Id="TARGETDIR" Name="SourceDir">
      <Directory Id="ProgramFilesFolder">
        <Directory Id="INSTALLFOLDER" Name="$ProductName" />
      </Directory>
    </Directory>
  </Fragment>
  <Fragment>
    <ComponentGroup Id="AppFiles" Directory="INSTALLFOLDER">
"@

# Per-file components. GUID is derived deterministically from the relative
# path so repeated builds at the same version produce identical WXS — that
# avoids "Component rules violation" warnings on repair installs.
$files = Get-ChildItem -Path $sourcePath -Recurse -File
foreach ($file in $files) {
    $relPath = $file.FullName.Substring($sourcePath.Path.Length + 1).Replace("\", "/")
    # Stable GUID: SHA1(relPath + UpgradeCode) -> formatted as guid
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($relPath + $UpgradeCode)
    $sha = [System.Security.Cryptography.SHA1]::Create()
    $hash = $sha.ComputeHash($bytes)
    $guid = ([guid]::new($hash[0..15])).ToString()
    $sha.Dispose()
    $wxs += "      <Component Id='cmp_$($guid.Replace("-", "_"))' Guid='{$guid}'>`n"
    $wxs += "        <File Id='fil_$($guid.Replace("-", "_"))' Source='$($file.FullName)' KeyPath='yes' />`n"
    $wxs += "      </Component>`n"
}

$wxs += @"
    </ComponentGroup>
  </Fragment>
</Wix>
"@

Set-Content -Path (Join-Path $root $WxsFile) -Value $wxs -Encoding UTF8

# Build MSI
Push-Location $root
try {
    & candle.exe $WxsFile
    if ($LASTEXITCODE -ne 0) { throw "candle.exe failed (exit $LASTEXITCODE)" }
    & light.exe -ext WixUIExtension installer.wixobj -o $OutputMsi
    if ($LASTEXITCODE -ne 0) { throw "light.exe failed (exit $LASTEXITCODE)" }
    Write-Host "MSI created: $(Join-Path $root $OutputMsi)" -ForegroundColor Green
    Write-Host "Size: $([math]::Round((Get-Item $OutputMsi).Length / 1MB, 1)) MB"
} finally {
    Pop-Location
}
