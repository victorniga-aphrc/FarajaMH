#!/usr/bin/env bash

# Start the FarajaMH app with the local virtual environment.
# Usage:
#   chmod +x start.sh
#   ./start.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [ ! -d ".venv" ]; then
  echo "⚠️  .venv not found. Create it and install requirements first, e.g.:"
  echo "    python -m venv .venv"
  echo "    source .venv/bin/activate"
  echo "    pip install -r requirements.txt"
  exit 1
fi

echo "Activating virtual environment..."
source ".venv/bin/activate"

echo "Starting FarajaMH on http://localhost:5000 ..."
python app.py

