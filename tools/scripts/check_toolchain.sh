#!/usr/bin/env bash
# Toolchain self-test. Prints PASS/FAIL per check; exit code = number of failures.
# Usage: bash check_toolchain.sh [path/to/tt_branch_pred] [path/to/reference-kit]
# Without arguments it clones the 704 repo into ./tt_branch_pred and skips the kit checks.
set -u
export PATH="$HOME/riscv-tools/bin:$PATH"
T704="${1:-./tt_branch_pred}"
KIT="${2:-}"
fails=0
pass() { printf '  PASS  %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; fails=$((fails+1)); }
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT

echo "== 1. Tools"
v=$(riscv64-unknown-elf-gcc -dumpversion 2>/dev/null || echo none)
[ "$v" = "13.2.0" ] && pass "riscv64-unknown-elf-gcc $v" \
  || fail "riscv64-unknown-elf-gcc is '$v' (team standard 13.2.0; numbers will differ)"
riscv64-unknown-elf-gcc -print-multi-lib 2>/dev/null | grep -q '^rv32im/ilp32;' \
  && pass "rv32im/ilp32 multilib" || fail "rv32im/ilp32 multilib missing"
command -v spike >/dev/null && pass "spike: $(command -v spike)" || fail "spike not on PATH"
command -v dtc   >/dev/null && pass "dtc (Spike calls it at run time)" || fail "dtc missing: apt install device-tree-compiler"
for t in addr2line objdump nm; do
  command -v riscv64-unknown-elf-$t >/dev/null && pass "riscv64-unknown-elf-$t" || fail "riscv64-unknown-elf-$t missing"
done

echo "== 2. Spike exit protocol"
cat > "$work/crt0.S" <<'EOF'
  .section .text.init
  .global _start
_start:
  li sp, 0x80800000
  call main
  slli a0, a0, 1
  ori  a0, a0, 1
  la   t0, tohost
  sw   a0, 0(t0)
1: j 1b
  .data
  .align 6
  .global tohost
tohost: .dword 0
  .global fromhost
fromhost: .dword 0
EOF
printf 'ENTRY(_start)\nSECTIONS { . = 0x80000000; .text : { *(.text.init) *(.text*) } .data : { *(.data*) *(.sdata*) } .bss : { *(.bss*) *(.sbss*) } }\n' > "$work/link.ld"
echo 'int main(void){ return 3; }' > "$work/rc.c"
riscv64-unknown-elf-gcc -march=rv32im -mabi=ilp32 -O1 -nostdlib -nostartfiles \
  -T "$work/link.ld" -Wl,--no-warn-rwx-segments "$work/crt0.S" "$work/rc.c" -o "$work/rc.elf" 2>/dev/null
timeout 10 spike --isa=rv32im --priv=m -m0x80000000:0x10000000 "$work/rc.elf" 2>/dev/null; rc=$?
[ $rc -eq 3 ] && pass "main's return value reaches Spike's exit code" || fail "expected exit code 3, got $rc"
echo 'volatile int x; int main(void){ for(;;) x++; }' > "$work/inf.c"
riscv64-unknown-elf-gcc -march=rv32im -mabi=ilp32 -O1 -nostdlib -nostartfiles \
  -T "$work/link.ld" -Wl,--no-warn-rwx-segments "$work/crt0.S" "$work/inf.c" -o "$work/inf.elf" 2>/dev/null
timeout 20 spike --isa=rv32im --priv=m -m0x80000000:0x10000000 --instructions=100000 \
  --log-commits --log="$work/inf.log" "$work/inf.elf" 2>/dev/null; rc=$?
n=$(wc -l < "$work/inf.log")
[ $rc -eq 0 ] && [ "$n" -eq 100000 ] \
  && pass "--instructions cap stops a hung program (exit 0, so detect it by log length = cap)" \
  || fail "instruction cap: rc=$rc lines=$n"

echo "== 3. The 704 software flow"
if [ ! -d "$T704/func_sim" ]; then
  git clone --quiet --recursive https://github.com/TristanRobitaille/tt_branch_pred.git "$T704" \
    || fail "cannot clone the 704 repo"
fi
if [ -d "$T704/func_sim" ]; then
  (
    cd "$T704/func_sim"
    grep -q 'include cstdint' CMakeLists.txt || sed -i 's/-g")/-g -include cstdint")/' CMakeLists.txt
    cmake CMakeLists.txt >/dev/null && make >/dev/null 2>&1
  )
  a=$("$T704/func_sim/build/func_sim" "$T704/func_sim/spike_log.txt" | tail -1)
  [ "$a" = "Accuracy: 0.819827" ] && pass "committed log: $a" || fail "committed log: '$a' (want 0.819827)"
  ( cd "$T704/func_sim"
    riscv64-unknown-elf-gcc -O0 -march=rv32i_zicsr_zifencei -mabi=ilp32 -nostartfiles -nostdlib \
      -T link.ld start.S reference.c -o "$work/reference" 2>/dev/null
    spike --log="$work/ref.log" --log-commits --isa=rv32i_zicsr_zifencei --priv=m -m128 "$work/reference" )
  a=$("$T704/func_sim/build/func_sim" "$work/ref.log" | tail -1)
  [ "$a" = "Accuracy: 0.863887" ] && pass "rebuilt with GCC 13.2: $a" \
    || fail "rebuilt: '$a' (0.863887 with GCC 13.2; another GCC gives another number)"
fi

if [ -n "$KIT" ]; then
  echo "== 4. Starter kit"
  out="$work/sort"
  python3 "$KIT/bptrace/bptrace.py" "$KIT/examples/sort_and_search.c" -o "$out" --opt O1 --user-only >/dev/null 2>&1
  b=$(python3 -c "import json;print(json.load(open('$out/summary.json'))['branches'])" 2>/dev/null || echo none)
  [ "$b" = "46922" ] && pass "sort_and_search -O1: 46922 branches" || fail "sort_and_search branches = $b (want 46922)"
  for f in trace.txt tb_input.hex trace.mem trace_init.vh branches.csv summary.json; do
    [ -s "$out/$f" ] && pass "writes $f" || fail "missing $f"
  done
fi

echo "== $fails failure(s)"
exit $fails
