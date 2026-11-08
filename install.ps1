# Quick Convert - one-shot setup. Run from this folder: .\install.ps1
#   -ClassicMenu   also force the old top-level right-click menu (slower menus
#                  system-wide; by default the entry sits under "Show more
#                  options" and the fast Windows 11 menu is left alone).
param([switch]$ClassicMenu)
$ErrorActionPreference = 'Stop'

Write-Host "== Quick Convert setup ==" -ForegroundColor Cyan

Write-Host "[1/5] Installing Python packages (Pillow, pillow-heif, pdf2docx, PyMuPDF, py7zr, python-pptx, pywin32 and format plugins)..."
# Version floors matter: py7zr < 0.20.5 had a 7z path-traversal flaw (CVE-2022-44900).
python -m pip install --user --upgrade --quiet `
    "pillow>=10" "pillow-heif>=0.13" "pdf2docx>=0.5.6" "pymupdf>=1.23" "py7zr>=0.20.5" `
    "python-pptx>=0.6.21" "pywin32>=306" `
    "openpyxl>=3.1" "pyyaml>=6" "defusedxml>=0.7" "extract-msg>=0.58" `
    "rawpy>=0.24" "pillow-jxl-plugin>=1.3"
if ($LASTEXITCODE -ne 0) { throw "pip install failed (exit $LASTEXITCODE)" }

Write-Host "[2/5] Checking Pandoc (document conversions)..."
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

Write-Host "[3/5] Checking an Office engine (Word/Excel/PowerPoint files -> PDF, images)..."
$appPaths = 'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths'
function Test-OfficeApp($exe) {
    foreach ($hive in 'HKLM:', 'HKCU:') {
        if (Test-Path "$hive\$appPaths\$exe") { return $true }
    }
    return $false
}
$soffice = (Get-Command soffice -ErrorAction SilentlyContinue) -or
    (Test-Path "$env:ProgramFiles\LibreOffice\program\soffice.exe") -or
    (Test-Path "${env:ProgramFiles(x86)}\LibreOffice\program\soffice.exe")
if ($soffice) {
    Write-Host "  LibreOffice already installed."
} elseif ((Test-OfficeApp 'winword.exe') -and (Test-OfficeApp 'powerpnt.exe') -and
          (Test-OfficeApp 'excel.exe')) {
    Write-Host "  Microsoft Word, Excel and PowerPoint found - will use them."
} else {
    Write-Host "  Word, Excel or PowerPoint missing - installing LibreOffice..."
    winget install -e --id TheDocumentFoundation.LibreOffice --silent `
        --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Warning ("LibreOffice install did not succeed (winget exit $LASTEXITCODE). " +
            "Presentation conversions need PowerPoint or LibreOffice: " +
            "winget install TheDocumentFoundation.LibreOffice")
    }
}

Write-Host "[4/5] Registering right-click menu entry..."
$regArgs = @('install')
if ($ClassicMenu) { $regArgs += '--classic-menu' }
python "$PSScriptRoot\register.py" @regArgs
if ($LASTEXITCODE -ne 0) { throw "registration failed (exit $LASTEXITCODE)" }

Write-Host "[5/5] Refreshing Explorer..."
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
