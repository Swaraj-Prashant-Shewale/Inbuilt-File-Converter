@echo off
setlocal EnableExtensions
title Quick Convert - Install
echo ================================================
echo    Installing Quick Convert
echo ================================================
echo.

rem --- Optional helpers: FFmpeg (video/audio) and Pandoc (documents) ---
where winget >nul 2>nul
if errorlevel 1 (
    echo winget was not found on this PC.
    echo Video/audio and document conversions need FFmpeg and Pandoc.
    echo Install them manually if you need those formats:
    echo     FFmpeg:  https://www.gyan.dev/ffmpeg/builds/
    echo     Pandoc:  https://pandoc.org/installing.html
    echo.
    goto register
)

echo Checking FFmpeg (needed for video/audio)...
where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo   installing FFmpeg via winget...
    winget install -e --id Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements
) else (
    echo   already installed.
)
echo.

echo Checking Pandoc (needed for documents)...
where pandoc >nul 2>nul
if not errorlevel 1 goto pandoc_ok
if exist "%LOCALAPPDATA%\Pandoc\pandoc.exe" goto pandoc_ok
echo   installing Pandoc via winget...
winget install -e --id JohnMacFarlane.Pandoc --silent --accept-package-agreements --accept-source-agreements
goto pandoc_done
:pandoc_ok
echo   already installed.
:pandoc_done
echo.

echo Checking Office engine (DOCX/PPTX to PDF, slide images)...
where soffice >nul 2>nul
if not errorlevel 1 goto lo_ok
if exist "%ProgramFiles%\LibreOffice\program\soffice.exe" goto lo_ok
if exist "%ProgramFiles(x86)%\LibreOffice\program\soffice.exe" goto lo_ok
set "APPS=SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
set HAVE_WORD=0
set HAVE_PPT=0
reg query "HKLM\%APPS%\winword.exe" >nul 2>nul && set HAVE_WORD=1
reg query "HKCU\%APPS%\winword.exe" >nul 2>nul && set HAVE_WORD=1
reg query "HKLM\%APPS%\powerpnt.exe" >nul 2>nul && set HAVE_PPT=1
reg query "HKCU\%APPS%\powerpnt.exe" >nul 2>nul && set HAVE_PPT=1
if "%HAVE_WORD%%HAVE_PPT%"=="11" goto office_ok
echo   Word and/or PowerPoint not found - installing LibreOffice...
winget install -e --id TheDocumentFoundation.LibreOffice --silent --accept-package-agreements --accept-source-agreements
goto office_done
:lo_ok
echo   LibreOffice already installed.
goto office_done
:office_ok
echo   Microsoft Word and PowerPoint found - will use them.
:office_done
echo.

:register
echo Registering the right-click menu entry...
"%~dp0QuickConvert.exe" --install

echo Refreshing Explorer so the entry appears at the top of the menu...
taskkill /f /im explorer.exe >nul 2>nul
start explorer.exe

echo.
echo Done. Right-click any file or folder, open "Show more options"
echo (or press Shift+F10), and choose "Quick Convert".
echo (You can close this window.)
pause
endlocal
