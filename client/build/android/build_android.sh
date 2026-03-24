#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

if command -v python3 >/dev/null 2>&1; then
  python3 -m pip install Pillow cairosvg || echo "WARN: logo renderer dependencies failed. Using existing icon assets."
  if ! python3 build/assets/prepare_logo_assets.py; then
    echo "WARN: logo asset preparation failed. Using existing icon assets."
  fi
fi

cd android

START_URL="${START_URL:-http://10.0.2.2:18765}"
GRADLE_CMD=""

if command -v gradle >/dev/null 2>&1; then
  GRADLE_CMD="gradle"
elif [[ -x "./gradlew" ]]; then
  GRADLE_CMD="./gradlew"
else
  GRADLE_VERSION="8.7"
  CACHE_DIR="${HOME}/.cache/kabootar/gradle-${GRADLE_VERSION}"
  GRADLE_BIN="${CACHE_DIR}/gradle-${GRADLE_VERSION}/bin/gradle"
  if [[ ! -x "${GRADLE_BIN}" ]]; then
    echo "Gradle not found. Downloading Gradle ${GRADLE_VERSION}..."
    mkdir -p "${CACHE_DIR}"
    ZIP_PATH="${CACHE_DIR}/gradle.zip"
    curl -fsSL "https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip" -o "${ZIP_PATH}"
    rm -rf "${CACHE_DIR}/gradle-${GRADLE_VERSION}"
    unzip -oq "${ZIP_PATH}" -d "${CACHE_DIR}"
  fi
  GRADLE_CMD="${GRADLE_BIN}"
fi

"${GRADLE_CMD}" :app:assembleDebug :app:assembleRelease -PstartUrl="${START_URL}"

if [[ -f "app/build/outputs/apk/release/app-release-unsigned.apk" ]]; then
  cp -f "app/build/outputs/apk/release/app-release-unsigned.apk" "app/build/outputs/apk/release/kabootar-client-android-universal.apk"
fi

echo "Debug APK: app/build/outputs/apk/debug/app-debug.apk"
echo "Universal APK: app/build/outputs/apk/release/kabootar-client-android-universal.apk"
