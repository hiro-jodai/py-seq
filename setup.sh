#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo ""
echo "=============================================="
echo " setup done! run:"
echo "   .venv/bin/python run.py"
echo " then open http://<this-machine>:8000 in a browser"
echo "=============================================="
