@echo off
setlocal

REM Run on Windows with Python installed
cd /d %~dp0\..\..

set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py"
if "%PYTHON_CMD%"=="" (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if "%PYTHON_CMD%"=="" (
  echo ERROR: Python not found in PATH.
  exit /b 1
)

"%PYTHON_CMD%" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
"%PYTHON_CMD%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo WARN: requirements.txt install failed. Falling back to core dependencies.
  "%PYTHON_CMD%" -m pip install Flask==3.0.3 SQLAlchemy==2.0.36 alembic==1.14.1 python-dotenv==1.0.1 requests==2.32.3 PySocks==1.7.1 feedparser==6.0.11 dnslib==0.9.25 dnspython==2.6.1
  if errorlevel 1 exit /b 1
)
"%PYTHON_CMD%" -m pip install pywebview pyinstaller
if errorlevel 1 exit /b 1
"%PYTHON_CMD%" -m pip install Pillow cairosvg
if errorlevel 1 exit /b 1
"%PYTHON_CMD%" build\assets\prepare_logo_assets.py
if errorlevel 1 exit /b 1
call npm.cmd --prefix frontend ci
if errorlevel 1 exit /b 1
call npm.cmd --prefix frontend run build
if errorlevel 1 exit /b 1

"%PYTHON_CMD%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name kabootar-client ^
  --icon "build\windows\kabootar.ico" ^
  --add-data "frontend\templates;frontend\templates" ^
  --add-data "frontend\static;frontend\static" ^
  --add-data "alembic;alembic" ^
  desktop_client.py
if errorlevel 1 exit /b 1

echo.
echo Build finished: dist\kabootar-client.exe
endlocal
