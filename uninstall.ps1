# Quick Convert - removes the context-menu entry and (if we added it) the
# classic-menu tweak. Run from this folder: .\uninstall.ps1
$ErrorActionPreference = 'Stop'

python "$PSScriptRoot\register.py" uninstall
if ($LASTEXITCODE -ne 0) { throw "unregistration failed (exit $LASTEXITCODE)" }

Write-Host "Restarting Explorer..."
Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
if (-not (Get-Process explorer -ErrorAction SilentlyContinue)) { Start-Process explorer }

Write-Host "Quick Convert removed. (Python packages were left installed.)" -ForegroundColor Green
