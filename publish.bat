@echo off
setlocal enabledelayedexpansion

where git >nul 2>nul || (echo Git is not installed. Run: winget install Git.Git & pause & exit /b 1)
where gh >nul 2>nul || (echo GitHub CLI is not installed. Run: winget install GitHub.cli & pause & exit /b 1)
gh auth status >nul 2>nul || (echo You are not signed in to GitHub. Run: gh auth login & pause & exit /b 1)

if not exist version.txt (echo version.txt is missing. & pause & exit /b 1)
if not exist release-notes.md (echo release-notes.md is missing. & pause & exit /b 1)
set /p VERSION=<version.txt
if "%VERSION%"=="" (echo version.txt is empty. & pause & exit /b 1)

gh release view %VERSION% >nul 2>nul
if not errorlevel 1 (echo Release %VERSION% already exists. Change version.txt to a new number and try again. & pause & exit /b 1)

echo Publishing %VERSION% ...

rem 1. Build first so nothing is pushed if the build fails
call build.bat nopause
if not exist dist\ImageLibrary.exe (echo Build failed. & pause & exit /b 1)

rem 2. Release notes = release-notes.md + SmartScreen note + checksum
for /f %%H in ('powershell -NoProfile -Command "(Get-FileHash dist\ImageLibrary.exe -Algorithm SHA256).Hash"') do set HASH=%%H
copy /y release-notes.md dist\release-notes.md >nul
>> dist\release-notes.md echo.
>> dist\release-notes.md echo ---
>> dist\release-notes.md echo.
>> dist\release-notes.md echo Windows may show a SmartScreen warning because the exe is not code-signed. Click More info, then Run anyway.
>> dist\release-notes.md echo.
>> dist\release-notes.md echo SHA256: %HASH%

rem 3. Commit and push the code
if not exist .git (
    git init
    git branch -M main
)
git add .
git commit -m "Release %VERSION%"

git remote get-url origin >nul 2>nul
if errorlevel 1 (
    set VIS=private
    set /p VIS=Make the new repo public or private? [private]: 
    if /i not "!VIS!"=="public" set VIS=private
    gh repo create image-library --!VIS! --source=. --remote=origin --push
    if errorlevel 1 (echo Could not create the repo. & pause & exit /b 1)
) else (
    git push -u origin main
    if errorlevel 1 (echo Push failed. & pause & exit /b 1)
)

rem 4. Publish the release
gh release create %VERSION% dist\ImageLibrary.exe --title "%VERSION%" --notes-file dist\release-notes.md
if errorlevel 1 (echo Release failed. & pause & exit /b 1)

echo.
echo Done. Code pushed and release %VERSION% published.
echo SHA256: %HASH%
pause
