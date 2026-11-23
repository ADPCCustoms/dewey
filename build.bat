@echo off
echo Installing requirements...
pip install -r requirements.txt pyinstaller

rem Optional: drag-and-drop support. The app works without it.
pip install tkinterdnd2
set DND=
python -c "import tkinterdnd2" 2>nul && set DND=--collect-all tkinterdnd2

set ICON=
if exist icon.ico set ICON=--icon icon.ico --add-data "icon.ico;."

echo Building ImageLibrary.exe...
pyinstaller --onefile --windowed --name ImageLibrary %ICON% %DND% image_library.py

echo.
echo Done. Your exe is in the dist folder.
if /i not "%1"=="nopause" pause
