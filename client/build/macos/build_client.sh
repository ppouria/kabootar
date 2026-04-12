#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

python3 -m pip install --upgrade pip
if ! python3 -m pip install -r requirements.txt; then
  echo "WARN: requirements.txt install failed. Falling back to core dependencies."
  python3 -m pip install \
    Flask==3.0.3 \
    SQLAlchemy==2.0.36 \
    alembic==1.14.1 \
    python-dotenv==1.0.1 \
    requests==2.32.3 \
    PySocks==1.7.1 \
    feedparser==6.0.11 \
    dnslib==0.9.25 \
    dnspython==2.6.1
fi
python3 -m pip install pywebview pyinstaller Pillow cairosvg

python3 build/assets/prepare_logo_assets.py

npm --prefix frontend ci
npm --prefix frontend run build

VERSION_NAME="$(sed -n 's/^version_name=//p' ../version.properties | head -n 1 | tr -d '\r\n')"
VERSION_NAME="${VERSION_NAME:-0.0.0}"
SAFE_NAME="$(printf '%s' "${VERSION_NAME}" | tr -cs '[:alnum:]._-' '-' | sed 's/^-*//; s/-*$//')"
SAFE_NAME="${SAFE_NAME:-0.0.0}"
VERSION_TAG="v${SAFE_NAME}"

ARCH_RAW="$(uname -m)"
case "${ARCH_RAW}" in
  x86_64|amd64) OUT_ARCH="amd64" ;;
  arm64|aarch64) OUT_ARCH="arm64" ;;
  *) OUT_ARCH="${ARCH_RAW}" ;;
esac

python3 -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --windowed \
  --name kabootar-darwin \
  --icon build/macos/kabootar.icns \
  --paths "vendor/python" \
  --hidden-import persian_encoder \
  --hidden-import persian_encoder.seed_words \
  --hidden-import persian_encoder.large_words \
  --add-data "../version.properties:." \
  --add-data "frontend/templates:frontend/templates" \
  --add-data "frontend/static:frontend/static" \
  --add-data "app/db/alembic:app/db/alembic" \
  --add-data "vendor/python/persian_encoder/data:persian_encoder/data" \
  --collect-all persian_encoder \
  desktop_client.py

OUT_NAME="Kabootar-client-darwin-${OUT_ARCH}-${VERSION_TAG}"
cp dist/kabootar-darwin "dist/${OUT_NAME}"
chmod +x "dist/${OUT_NAME}"

echo
echo "Build finished: dist/${OUT_NAME}"
