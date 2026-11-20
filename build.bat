@echo off
echo Installing requirements...
pip install -r requirements.txt pyinstaller

set ICON=
if exist icon.ico set ICON=--icon icon.ico

echo Building ImageLibrary.exe...
pyinstaller --onefile --windowed --name ImageLibrary %ICON% image_library.py

echo.
echo Done. Your exe is in the dist folder.
if /i not "%1"=="nopause" pause
