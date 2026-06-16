#!/bin/bash
if [ ! -f ".venv/bin/activate" ]; then
  echo "Virtual environment not found. Run ./setup.sh first."
  exit 1
fi
source "$(dirname "$0")/.venv/bin/activate"
PORT=${1:-8500}
python -c "from server import run; run(port=$PORT)"
