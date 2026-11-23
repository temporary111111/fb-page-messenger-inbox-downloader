@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0dist\InboxArchive\InboxArchive.exe" (
  start "" "%~dp0dist\InboxArchive\InboxArchive.exe" --app-dir "%~dp0."
  exit /b 0
)
if exist "%~dp0.venv\Scripts\pythonw.exe" (
  start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0desktop_entry.py" --app-dir "%~dp0."
  exit /b 0
)
start "" pythonw.exe "%~dp0desktop_entry.py" --app-dir "%~dp0."
