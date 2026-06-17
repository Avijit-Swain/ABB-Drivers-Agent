#!/bin/bash
set -e

echo "Creating virtual environment..."
python3 -m venv .venv

echo "Installing dependencies..."
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt

echo ""
echo "Setup complete. Run with:"
echo "  ./run_server.sh       # React UI (default port 8500)"
echo "  ./run_server.sh 8503  # React UI on a custom port"
