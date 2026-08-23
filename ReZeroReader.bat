@echo off
rem Launch Re:Zero Reader as a native desktop app (no browser, no lingering console).
cd /d "%~dp0"

if not exist ".\.venv\Scripts\pythonw.exe" (
  echo The environment is not set up yet.
  echo Right-click setup.ps1 ^> Run with PowerShell   (one time only^)
  pause
  exit /b 1
)

rem pythonw = windowless; "start" lets this launcher close immediately.
rem If the app window never appears, check app.log in this folder.
start "" ".\.venv\Scripts\pythonw.exe" -m src.desktop
exit /b 0
