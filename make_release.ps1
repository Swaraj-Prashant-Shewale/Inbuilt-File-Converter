# Build the downloadable release assets for BOTH editions:
#   * QuickConvert-Source-v<ver>.zip      - Python scripts (needs Python + FFmpeg)
#   * QuickConvert-Standalone-v<ver>.zip  - bundled exe (no Python needed)
#
# The standalone zip is only produced if the PyInstaller bundle exists, so run
# .\build_exe.ps1 first for a full release. dist\ is git-ignored; the zips are
# uploaded to the GitHub release, not committed.
#
# Usage:  .\make_release.ps1              # version 1.0.0
#         .\make_release.ps1 -Version 1.1.0
param([string]$Version = "1.0.0")
$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$dist = Join-Path $root 'dist'
New-Item -ItemType Directory -Force -Path $dist | Out-Null

function New-Zip($stageDir, $zipPath) {
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Compress-Archive -Path $stageDir -DestinationPath $zipPath
}

function Stage($name) {
    $stageRoot = Join-Path ([IO.Path]::GetTempPath()) "qc-$name-$Version"
    if (Test-Path $stageRoot) { Remove-Item $stageRoot -Recurse -Force }
    $stage = Join-Path $stageRoot 'QuickConvert'
    New-Item -ItemType Directory -Force -Path $stage | Out-Null
    return @($stageRoot, $stage)
}

# Remove any stale single-edition zip from the earlier build.
Get-ChildItem $dist -Filter "QuickConvert-v*.zip" -ErrorAction SilentlyContinue |
    Remove-Item -Force

# ---- Edition A: Source (requires Python) ---------------------------------
$srcFiles = @('converter.py', 'engines.py', 'register.py', 'install.ps1',
              'uninstall.ps1', 'README.md', 'LICENSE', 'CHANGELOG.md')
foreach ($f in $srcFiles) {
    if (-not (Test-Path (Join-Path $root $f))) { throw "missing file: $f" }
}
$sr, $st = Stage 'src'
foreach ($f in $srcFiles) { Copy-Item (Join-Path $root $f) $st }
$srcZip = Join-Path $dist "QuickConvert-Source-v$Version.zip"
New-Zip $st $srcZip
Remove-Item $sr -Recurse -Force
Write-Host "Built $srcZip" -ForegroundColor Green

# ---- Edition B: Standalone (no Python) -----------------------------------
$bundle = Join-Path $root 'pyi_dist\QuickConvert'
if (Test-Path (Join-Path $bundle 'QuickConvert.exe')) {
    $sr, $st = Stage 'sa'
    Copy-Item (Join-Path $bundle '*') $st -Recurse -Force
    foreach ($f in @('README.md', 'LICENSE', 'CHANGELOG.md',
                     'packaging\Install.bat', 'packaging\Uninstall.bat')) {
        Copy-Item (Join-Path $root $f) $st
    }
    $saZip = Join-Path $dist "QuickConvert-Standalone-v$Version.zip"
    New-Zip $st $saZip
    Remove-Item $sr -Recurse -Force
    Write-Host ("Built $saZip ({0:N1} MB)" -f ((Get-Item $saZip).Length / 1MB)) `
        -ForegroundColor Green
} else {
    Write-Warning "Standalone bundle not found - run .\build_exe.ps1 first. Skipped."
}

Write-Host ""
Write-Host "Assets ready in dist\:" -ForegroundColor Cyan
Get-ChildItem $dist -Filter *.zip |
    ForEach-Object { "  {0,-40} {1,7:N1} MB" -f $_.Name, ($_.Length / 1MB) }
