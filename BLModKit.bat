@echo off
rem Opens the blmodkit window. Needs Python 3.9+ with numpy and Pillow (pip install -r requirements.txt).
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0blmodkit_gui.pyw"
) else (
    python "%~dp0blmodkit_gui.pyw"
    if errorlevel 1 pause
)
