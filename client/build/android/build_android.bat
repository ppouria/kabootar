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
"%PYTHON_CMD%" -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('PIL') and importlib.util.find_spec('cairosvg') else 1)"
if errorlevel 1 (
  "%PYTHON_CMD%" -m pip install Pillow cairosvg
  if errorlevel 1 (
    echo WARN: logo renderer dependencies failed. Using existing icon assets.
  )
)
"%PYTHON_CMD%" build\assets\prepare_logo_assets.py
if errorlevel 1 (
  echo WARN: logo asset preparation failed. Using existing icon assets.
)

cd android

if defined KABOOTAR_BUILD_PYTHON (
  call "!KABOOTAR_BUILD_PYTHON!" -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else 1)" >nul 2>nul
  if errorlevel 1 (
    echo ERROR: KABOOTAR_BUILD_PYTHON must point to a Python 3.13 executable.
    exit /b 1
  )
) else (
  for /f "delims=" %%P in ('py -3.13 -c "import sys; print(sys.executable)" 2^>nul') do set "KABOOTAR_BUILD_PYTHON=%%P"
  if not defined KABOOTAR_BUILD_PYTHON (
    for /f "delims=" %%P in ('python -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else 1); print(sys.executable)" 2^>nul') do set "KABOOTAR_BUILD_PYTHON=%%P"
  )
)
if not defined KABOOTAR_BUILD_PYTHON (
  echo ERROR: Python 3.13 is required for Android builds because Chaquopy is configured for Python 3.13.
  echo Install Python 3.13 and make sure ^`py -3.13^` works, or set KABOOTAR_BUILD_PYTHON to a Python 3.13 executable.
  exit /b 1
)

set "RUN_GRADLE="
set "JDK_CACHE=%USERPROFILE%\.cache\kabootar\jdk-17"
set "JDK_ZIP=%JDK_CACHE%\temurin-jdk-17.zip"
set "JDK_HOME="
set "PF64=%ProgramW6432%"
if not defined PF64 set "PF64=%ProgramFiles%"
if defined JAVA_HOME (
  if exist "%JAVA_HOME%\bin\java.exe" (
    set "JDK_HOME=%JAVA_HOME%"
    goto :jdk_ready
  )
)
for /d %%D in ("%PF64%\Eclipse Adoptium\jdk-17*" "%PF64%\Java\jdk-17*" "%PF64%\Microsoft\jdk-17*" "%PF64%\Zulu\zulu-17*" "%ProgramFiles%\Eclipse Adoptium\jdk-17*" "%ProgramFiles%\Java\jdk-17*" "%ProgramFiles%\Microsoft\jdk-17*" "%ProgramFiles%\Zulu\zulu-17*" "%ProgramFiles(x86)%\Eclipse Adoptium\jdk-17*" "%ProgramFiles(x86)%\Java\jdk-17*" "%ProgramFiles(x86)%\Microsoft\jdk-17*" "%ProgramFiles(x86)%\Zulu\zulu-17*") do (
  if exist "%%~fD\bin\java.exe" (
    set "JDK_HOME=%%~fD"
    goto :jdk_ready
  )
)
for /d %%D in ("!JDK_CACHE!\jdk-*") do (
  if exist "%%~fD\bin\java.exe" (
    set "JDK_HOME=%%~fD"
    goto :jdk_ready
  )
)
if not defined JDK_HOME if exist "!JDK_ZIP!" (
  echo Reusing cached Temurin JDK 17 archive...
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference='Stop';" ^
    "$cache='!JDK_CACHE!';" ^
    "Get-ChildItem -Path $cache -Directory -Filter 'jdk-*' | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue;" ^
    "Expand-Archive -Path '!JDK_ZIP!' -DestinationPath $cache -Force"
  if errorlevel 1 (
    del /f /q "!JDK_ZIP!" >nul 2>nul
  ) else (
    for /d %%D in ("!JDK_CACHE!\jdk-*") do (
      if exist "%%~fD\bin\java.exe" (
        set "JDK_HOME=%%~fD"
        goto :jdk_ready
      )
    )
  )
)
if not defined JDK_HOME (
  echo Downloading Temurin JDK 17...
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference='Stop';" ^
    "$cache='!JDK_CACHE!';" ^
    "$zip='!JDK_ZIP!';" ^
    "New-Item -ItemType Directory -Force -Path $cache | Out-Null;" ^
    "Get-ChildItem -Path $cache -Directory -Filter 'jdk-*' | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue;" ^
    "Invoke-WebRequest -Uri 'https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse' -OutFile $zip;" ^
    "Expand-Archive -Path $zip -DestinationPath $cache -Force;" ^
    "Remove-Item -Force $zip"
  if errorlevel 1 exit /b 1
  for /d %%D in ("!JDK_CACHE!\jdk-*") do (
    if exist "%%~fD\bin\java.exe" (
      set "JDK_HOME=%%~fD"
      goto :jdk_ready
    )
  )
)
:jdk_ready
if not defined JDK_HOME (
  echo ERROR: JDK 17 is required for Android builds. Install JDK 17 and set JAVA_HOME.
  exit /b 1
)
set "JAVA_HOME=!JDK_HOME!"
set "PATH=!JAVA_HOME!\bin;!PATH!"

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

call "%RUN_GRADLE%" :app:clean :app:assembleDebug :app:assembleRelease
if errorlevel 1 exit /b 1

set "RELEASE_DIR=app\build\outputs\apk\release"
set "UNIVERSAL_SOURCE=%RELEASE_DIR%\app-universal-release.apk"
if not exist "%UNIVERSAL_SOURCE%" set "UNIVERSAL_SOURCE=%RELEASE_DIR%\app-release.apk"
set "ARM64_SOURCE=%RELEASE_DIR%\app-arm64-v8a-release.apk"
set "X64_SOURCE=%RELEASE_DIR%\app-x86_64-release.apk"

if not exist "%UNIVERSAL_SOURCE%" (
  echo ERROR: universal release APK not found
  exit /b 1
)
if not exist "%ARM64_SOURCE%" (
  echo ERROR: arm64-v8a release APK not found
  exit /b 1
)
if not exist "%X64_SOURCE%" (
  echo ERROR: x86_64 release APK not found
  exit /b 1
)
copy /Y "%UNIVERSAL_SOURCE%" "%RELEASE_DIR%\kabootar-android-universal.apk" >nul
copy /Y "%ARM64_SOURCE%" "%RELEASE_DIR%\kabootar-android-arm64-v8a.apk" >nul
copy /Y "%X64_SOURCE%" "%RELEASE_DIR%\kabootar-android-x86_64.apk" >nul

echo.
echo Debug universal APK: app\build\outputs\apk\debug\app-universal-debug.apk
echo Debug ARM64 APK: app\build\outputs\apk\debug\app-arm64-v8a-debug.apk
echo Debug x86_64 APK: app\build\outputs\apk\debug\app-x86_64-debug.apk
echo Universal APK: %RELEASE_DIR%\kabootar-android-universal.apk
echo ARM64 APK: %RELEASE_DIR%\kabootar-android-arm64-v8a.apk
echo x86_64 APK: %RELEASE_DIR%\kabootar-android-x86_64.apk
endlocal
