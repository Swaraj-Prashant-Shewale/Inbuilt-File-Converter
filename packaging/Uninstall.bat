@echo off
setlocal EnableExtensions
title Quick Convert - Uninstall
echo Removing the Quick Convert right-click entry...
"%~dp0QuickConvert.exe" --uninstall

echo Refreshing Explorer...
taskkill /f /im explorer.exe >nul 2>nul
start explorer.exe

echo.
echo Quick Convert has been removed. You can now delete this folder.
echo (FFmpeg and Pandoc, if they were installed, were left in place.)
pause
endlocal
