#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
export PYTHONPATH="$(pwd)"
./.venv/bin/python manage.py migrate
echo "Setup done. Run web: .venv/bin/python manage.py web"
