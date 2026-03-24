@echo off
setlocal

cd /d %~dp0\..\..

where py >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python launcher not found in PATH.
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  py -m venv .venv
  if errorlevel 1 exit /b 1
)

call .venv\Scripts\activate
if errorlevel 1 exit /b 1

py -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

call npm --prefix frontend ci
if errorlevel 1 exit /b 1

call npm --prefix frontend run build
if errorlevel 1 exit /b 1

set PYTHONUNBUFFERED=1
set APP_HOST=127.0.0.1
set APP_PORT=8090

echo Running debug web server at http://127.0.0.1:8090
echo Press Ctrl+C to stop.
py manage.py web

endlocal
