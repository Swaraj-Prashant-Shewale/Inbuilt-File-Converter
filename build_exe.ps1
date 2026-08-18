# Build the standalone (no-Python) Quick Convert bundle with PyInstaller.
# Output: pyi_dist\QuickConvert\QuickConvert.exe  (a onedir bundle).
$ErrorActionPreference = 'Stop'

Write-Host "Building standalone Quick Convert (PyInstaller)..." -ForegroundColor Cyan

# The exe embeds this icon; generate it if it isn't there yet.
if (-not (Test-Path "$PSScriptRoot\quickconvert.ico")) {
    python -c "import sys; sys.path.insert(0, r'$PSScriptRoot'); import register; register.make_icon()"
}

python -m PyInstaller --noconfirm --clean `
    --distpath "$PSScriptRoot\pyi_dist" `
    --workpath "$PSScriptRoot\pyi_build" `
    "$PSScriptRoot\QuickConvert.spec"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed (exit $LASTEXITCODE)" }

$exe = "$PSScriptRoot\pyi_dist\QuickConvert\QuickConvert.exe"
if (-not (Test-Path $exe)) { throw "expected exe not produced: $exe" }
$size = [math]::Round((Get-ChildItem "$PSScriptRoot\pyi_dist\QuickConvert" -Recurse |
    Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "Built $exe" -ForegroundColor Green
Write-Host "Bundle size: $size MB"
