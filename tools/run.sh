#!/usr/bin/env bash
# Start the branch predictor app.   bash run.sh
# First run: creates a Python environment in .venv and installs the app's packages (about a minute).
# Then open http://localhost:8501 in your Windows browser.
set -euo pipefail
cd "$(dirname "$0")"

export SPIKE="${SPIKE:-$HOME/riscv-tools/bin/spike}"
export PATH="$(dirname "$SPIKE"):$PATH"

missing=()
command -v riscv64-unknown-elf-gcc >/dev/null || missing+=("riscv64-unknown-elf-gcc")
[ -x "$SPIKE" ] || command -v spike >/dev/null || missing+=("spike")
command -v dtc >/dev/null || missing+=("dtc")
if [ ${#missing[@]} -gt 0 ]; then
  echo "Note: ${missing[*]} not found. The app will only run the built-in examples (precomputed traces)."
  echo "      To compile your own C files, install the toolchain once:  bash scripts/setup_toolchain.sh"
fi

if [ ! -x .venv/bin/streamlit ]; then
  echo "Setting up the Python environment in .venv (first run only)..."
  python3 -m venv .venv || { echo "Run: sudo apt install -y python3-venv"; exit 1; }
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r app/requirements.txt
fi

echo "Open http://localhost:8501 in your browser. Stop with Ctrl+C."
exec .venv/bin/streamlit run app/app.py --server.headless true --server.port "${PORT:-8501}"
