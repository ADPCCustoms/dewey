@echo off
setlocal enabledelayedexpansion

where git >nul 2>nul || (echo Git is not installed. Run: winget install Git.Git & pause & exit /b 1)
where gh >nul 2>nul || (echo GitHub CLI is not installed. Run: winget install GitHub.cli & pause & exit /b 1)
gh auth status >nul 2>nul || (echo You are not signed in to GitHub. Run: gh auth login & pause & exit /b 1)

set /p VERSION=Version tag for this release, for example v1.0: 
if "%VERSION%"=="" (echo No version entered. & pause & exit /b 1)

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
)

call build.bat nopause
if not exist dist\ImageLibrary.exe (echo Build failed. & pause & exit /b 1)

gh release create %VERSION% dist\ImageLibrary.exe --title "%VERSION%" --notes "Release %VERSION%"
if errorlevel 1 (echo Release failed - the tag may already exist. & pause & exit /b 1)

echo.
echo Done. Code pushed and release %VERSION% published.
pause
