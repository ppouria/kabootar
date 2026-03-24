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

npm --prefix frontend install
npm --prefix frontend run build

python3 -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --windowed \
  --name kabootar-client \
  --add-data "frontend/templates:frontend/templates" \
  --add-data "frontend/static:frontend/static" \
  --add-data "alembic:alembic" \
  desktop_client.py

stage_dir="dist/kabootar-client-linux-x64"
rm -rf "$stage_dir"
mkdir -p "$stage_dir"
cp dist/kabootar-client "$stage_dir/"
cp build/linux/kabootar.png "$stage_dir/"
cp README.md "$stage_dir/"
tar -C dist -czf dist/kabootar-client-linux-x64.tar.gz kabootar-client-linux-x64

echo
echo "Build finished: dist/kabootar-client-linux-x64.tar.gz"
