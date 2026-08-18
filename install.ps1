# Quick Convert - one-shot setup. Run from this folder: .\install.ps1
#   -ClassicMenu   also force the old top-level right-click menu (slower menus
#                  system-wide; by default the entry sits under "Show more
#                  options" and the fast Windows 11 menu is left alone).
param([switch]$ClassicMenu)
$ErrorActionPreference = 'Stop'

Write-Host "== Quick Convert setup ==" -ForegroundColor Cyan

Write-Host "[1/4] Installing Python packages (Pillow, pillow-heif, pdf2docx, PyMuPDF, py7zr)..."
# Version floors matter: py7zr < 0.20.5 had a 7z path-traversal flaw (CVE-2022-44900).
python -m pip install --user --upgrade --quiet `
    "pillow>=10" "pillow-heif>=0.13" "pdf2docx>=0.5.6" "pymupdf>=1.23" "py7zr>=0.20.5"
if ($LASTEXITCODE -ne 0) { throw "pip install failed (exit $LASTEXITCODE)" }

Write-Host "[2/4] Checking Pandoc (document conversions)..."
$pandoc = Get-Command pandoc -ErrorAction SilentlyContinue
if (-not $pandoc) {
    $localPandoc = Join-Path $env:LOCALAPPDATA 'Pandoc\pandoc.exe'
    if (Test-Path $localPandoc) { $pandoc = $localPandoc }
}
if (-not $pandoc) {
    # Native exe failures do NOT throw under $ErrorActionPreference='Stop', so
    # check the exit code explicitly rather than relying on try/catch.
    winget install -e --id JohnMacFarlane.Pandoc --silent `
        --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Warning ("Pandoc install did not succeed (winget exit $LASTEXITCODE). " +
            "Document conversions will prompt you to install it manually: " +
            "winget install JohnMacFarlane.Pandoc")
    }
} else {
    Write-Host "  Pandoc already installed."
}

Write-Host "[3/4] Registering right-click menu entry..."
$regArgs = @('install')
if ($ClassicMenu) { $regArgs += '--classic-menu' }
python "$PSScriptRoot\register.py" @regArgs
if ($LASTEXITCODE -ne 0) { throw "registration failed (exit $LASTEXITCODE)" }

Write-Host "[4/4] Refreshing Explorer..."
Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
if (-not (Get-Process explorer -ErrorAction SilentlyContinue)) { Start-Process explorer }

Write-Host ""
if ($ClassicMenu) {
    Write-Host "Done! Right-click any file and choose 'Quick Convert'." -ForegroundColor Green
} else {
    Write-Host "Done! Right-click any file, open 'Show more options' (or press" -ForegroundColor Green
    Write-Host "Shift+F10), and choose 'Quick Convert'." -ForegroundColor Green
}
