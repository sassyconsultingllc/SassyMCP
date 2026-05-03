# Build sassymcp.dxt from the existing PyInstaller exe + dxt/manifest.json.
#
# Prereqs:
#   - sassymcp.exe must already be built (run build.bat first, or this script will).
#   - dxt/manifest.json must exist and have a version matching sassymcp/__init__.py __version__.
#
# Output: dist/sassymcp-v<version>.dxt
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root "sassymcp.exe"
$manifestSrc = Join-Path $root "dxt\manifest.json"
$iconSrc = Join-Path $root "dxt\icon.png"
$readmeSrc = Join-Path $root "dxt\README.md"

if (-not (Test-Path $exe)) {
    Write-Host "sassymcp.exe not found, running build.bat..."
    & (Join-Path $root "build.bat")
    if (-not (Test-Path $exe)) {
        Write-Error "build.bat did not produce sassymcp.exe"
        exit 1
    }
}

# Read version from sassymcp/__init__.py
$initContent = Get-Content (Join-Path $root "sassymcp\__init__.py") -Raw
if ($initContent -match '__version__\s*=\s*"([^"]+)"') {
    $version = $Matches[1]
} else {
    Write-Error "Could not parse __version__ from sassymcp/__init__.py"
    exit 1
}
Write-Host "Building DXT for sassymcp v$version..."

# Stage to a tempdir
$staging = Join-Path $env:TEMP "sassymcp-dxt-staging-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $staging | Out-Null
try {
    New-Item -ItemType Directory -Path (Join-Path $staging "server") | Out-Null
    Copy-Item $exe (Join-Path $staging "server\sassymcp.exe")
    Copy-Item $manifestSrc (Join-Path $staging "manifest.json")
    if (Test-Path $iconSrc) { Copy-Item $iconSrc (Join-Path $staging "icon.png") }
    if (Test-Path $readmeSrc) { Copy-Item $readmeSrc (Join-Path $staging "README.md") }

    # Verify manifest version matches
    $manifestObj = Get-Content (Join-Path $staging "manifest.json") -Raw | ConvertFrom-Json
    if ($manifestObj.version -ne $version) {
        Write-Warning "manifest version $($manifestObj.version) != __version__ $version. Updating manifest."
        $manifestObj.version = $version
        $manifestObj | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $staging "manifest.json") -Encoding UTF8
    }

    # Zip
    $distDir = Join-Path $root "dist"
    New-Item -ItemType Directory -Path $distDir -Force | Out-Null
    $output = Join-Path $distDir "sassymcp-v$version.dxt"
    if (Test-Path $output) { Remove-Item $output -Force }
    Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $output -CompressionLevel Optimal
    Write-Host "Built: $output ($([math]::Round((Get-Item $output).Length / 1MB, 1)) MB)"
} finally {
    Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
}
