#!/usr/bin/env bash
# Rebuild precomputed/: example traces the app can use on machines without the RISC-V toolchain.
# Run on a machine with the toolchain after changing an example or bptrace.   bash scripts/make_precomputed.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export SPIKE="${SPIKE:-$HOME/riscv-tools/bin/spike}"
rm -rf precomputed && mkdir -p precomputed
for src in examples/*.c; do
  stem=$(basename "$src" .c)
  for opt in O0 O1 O2; do
    out="precomputed/${stem}_${opt}"
    python3 bptrace/bptrace.py "$src" -o "$out" --opt "$opt" --user-only > /dev/null 2>&1
    rm -f "$out"/spike.log "$out"/*.elf "$out"/*.dis "$out"/trace.bin
    cp "$src" "$out/"
    touch "$out/PRECOMPUTED"
    echo "$out: $(python3 -c "import json;print(json.load(open('$out/summary.json'))['branches'])") branches"
  done
done
