#!/bin/bash
if [ ! -f ".venv/bin/activate" ]; then
  echo "Virtual environment not found. Run ./setup.sh first."
  exit 1
fi
source "$(dirname "$0")/.venv/bin/activate"
streamlit run app.py
