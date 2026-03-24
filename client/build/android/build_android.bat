@echo off
setlocal EnableDelayedExpansion

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
"%PYTHON_CMD%" -m pip install Pillow cairosvg
if errorlevel 1 (
  echo WARN: logo renderer dependencies failed. Using existing icon assets.
)
"%PYTHON_CMD%" build\assets\prepare_logo_assets.py
if errorlevel 1 (
  echo WARN: logo asset preparation failed. Using existing icon assets.
)

cd android

if "%START_URL%"=="" set START_URL=http://10.0.2.2:18765

set "RUN_GRADLE="

where gradle >nul 2>nul
if errorlevel 1 (
  if exist gradlew.bat (
    set "RUN_GRADLE=gradlew.bat"
  ) else (
    set "GRADLE_VERSION=8.7"
    set "GRADLE_CACHE=%USERPROFILE%\.cache\kabootar\gradle-!GRADLE_VERSION!"
    set "GRADLE_BIN=!GRADLE_CACHE!\gradle-!GRADLE_VERSION!\bin\gradle.bat"
    if not exist "!GRADLE_BIN!" (
      echo Gradle not found. Downloading Gradle !GRADLE_VERSION!...
      powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "$ErrorActionPreference='Stop';" ^
        "$cache='!GRADLE_CACHE!';" ^
        "$zip=Join-Path $cache ('gradle-' + [Guid]::NewGuid().ToString('N') + '.zip');" ^
        "$target=Join-Path $cache 'gradle-!GRADLE_VERSION!';" ^
        "New-Item -ItemType Directory -Force -Path $cache | Out-Null;" ^
        "if (Test-Path $target) { Remove-Item -Recurse -Force $target };" ^
        "Invoke-WebRequest -Uri 'https://services.gradle.org/distributions/gradle-!GRADLE_VERSION!-bin.zip' -OutFile $zip;" ^
        "Expand-Archive -Path $zip -DestinationPath $cache -Force;" ^
        "Remove-Item -Force $zip"
      if errorlevel 1 exit /b 1
    )
    set "RUN_GRADLE=!GRADLE_BIN!"
  )
) else (
  set "RUN_GRADLE=gradle"
)

call "%RUN_GRADLE%" :app:clean :app:assembleDebug :app:assembleRelease -PstartUrl="%START_URL%"
if errorlevel 1 exit /b 1

set "UNIVERSAL_SOURCE=app\build\outputs\apk\release\app-release.apk"
if not exist "%UNIVERSAL_SOURCE%" (
  echo ERROR: signed release APK not found: %UNIVERSAL_SOURCE%
  exit /b 1
)
copy /Y "%UNIVERSAL_SOURCE%" "app\build\outputs\apk\release\kabootar-client-android-universal.apk" >nul

echo.
echo Debug APK: app\build\outputs\apk\debug\app-debug.apk
echo Universal APK: app\build\outputs\apk\release\kabootar-client-android-universal.apk
endlocal
