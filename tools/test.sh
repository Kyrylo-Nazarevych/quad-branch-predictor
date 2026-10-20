#!/usr/bin/env bash
# Check everything works.   bash test.sh
# 1. toolchain + 704 numbers + trace tool   2. reference models vs the published results   3. the app
set -uo pipefail
cd "$(dirname "$0")"
export SPIKE="${SPIKE:-$HOME/riscv-tools/bin/spike}"
export PATH="$(dirname "$SPIKE"):$PATH"
fail=0

echo "### 1. Toolchain (clones the 704 repo into ./tt_branch_pred the first time)"
bash scripts/check_toolchain.sh ./tt_branch_pred . || fail=1

echo; echo "### 2. Reference models against the published accuracies"
python3 stats/ref_models.py --selftest --kit . --t704 ./tt_branch_pred | cut -c1-40 || fail=1

echo; echo "### 3. App tests"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv && .venv/bin/pip install --quiet -r app/requirements.txt
fi
.venv/bin/python -m pytest app/test_app.py -q || fail=1

echo; [ $fail -eq 0 ] && echo "ALL PASSED" || echo "SOMETHING FAILED (see above)"
exit $fail
