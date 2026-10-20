#!/usr/bin/env python3
"""bptrace: C program -> RISC-V ELF -> Spike commit log -> branch trace files.

Usage:
    python3 bptrace.py examples/prog.c -o out/prog [--opt O1] [--march rv32im] [--user-only]

Produces in the output folder:
    prog.elf          compiled program (with debug info)
    prog.dis          disassembly (riscv64-unknown-elf-objdump -d)
    spike.log         raw Spike commit log
    trace.txt         one line per executed conditional branch:  <pc hex> <taken 0/1>
    trace.bin         same, 5 bytes per branch: little-endian uint32 pc, uint8 taken
    tb_input.hex      what the testbench / FPGA reads: one line per branch, "<pc slice hex> <taken>"
    trace.mem         same, for $readmemh: one hex word per branch = {taken, pc slice}
    trace_init.vh     same, as Verilog statements to `include (no file I/O in the simulator)
    branches.csv      one row per static branch: pc, count, taken, function, file:line, instruction
    summary.json      totals: instructions retired, branches, taken rate, MPKI denominator
"""
import argparse, collections, csv, json, os, re, struct, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
RT = os.path.join(HERE, "runtime")
PREFIX = os.environ.get("RISCV_PREFIX", "riscv64-unknown-elf-")
SPIKE = os.environ.get("SPIKE", "spike")

# Spike --log-commits line:  core   0: 3 0x80000194 (0xfe842703) x14 0x00000000 mem 0x807fffe8
COMMIT = re.compile(r"core\s+\d+:\s+\d+\s+0x([0-9a-fA-F]+)\s+\(0x([0-9a-fA-F]+)\)")
BRANCH_OPCODE = 0x63          # RISC-V B-type: beq bne blt bge bltu bgeu
FUNCT3 = {0: "beq", 1: "bne", 4: "blt", 5: "bge", 6: "bltu", 7: "bgeu"}


def run(cmd, **kw):
    print("+", " ".join(cmd), file=sys.stderr)
    return subprocess.run(cmd, check=True, **kw)


def compile_c(src, out_elf, opt, march):
    mabi = "ilp32e" if march.startswith("rv32e") else "ilp32"
    run([PREFIX + "gcc", f"-march={march}", f"-mabi={mabi}", opt, "-g",
         "-ffreestanding", "-fno-builtin", "-nostdlib", "-nostartfiles",
         "-T", os.path.join(RT, "link.ld"),
         os.path.join(RT, "crt0.S"), os.path.join(RT, "mini_libc.c"), src,
         "-lgcc", "-Wl,--no-warn-rwx-segments", "-o", out_elf])


def run_spike(elf, log, isa, max_steps, max_instructions):
    # Spike stops by itself when main() returns (crt0 writes to "tohost").
    # crt0 always writes 1, so the exit status is 0; main()'s value is read from the log instead.
    # --instructions caps a program that never returns. Spike then also exits with 0,
    # so main() detects the cap by the log length (see "timeout" below).
    cmd = [SPIKE, f"--isa={isa}", "--priv=m", "-m0x80000000:0x1000000",
           f"--instructions={max_instructions}", "--log-commits", f"--log={log}", elf]
    print("+", " ".join(cmd), file=sys.stderr)
    try:
        return subprocess.run(cmd, timeout=max_steps).returncode
    except subprocess.TimeoutExpired:
        print("Spike timed out: does main() return?", file=sys.stderr)
        return None


def extract(log, result_addr=None):
    """Return (list of (pc, insn), number of log lines, main()'s return value or None)."""
    seq, lines, result = [], 0, None
    store = re.compile(rf"mem 0x0*{result_addr:x} 0x([0-9a-fA-F]+)") if result_addr is not None else None
    with open(log, errors="replace") as f:
        for line in f:
            m = COMMIT.search(line)
            if m:
                lines += 1
                seq.append((int(m.group(1), 16), int(m.group(2), 16)))
                if store:
                    s = store.search(line)
                    if s:
                        v = int(s.group(1), 16)
                        result = v - (1 << 32) if v >= 1 << 31 else v
    return seq, lines, result


def symbol_addr(elf, name):
    out = subprocess.run([PREFIX + "nm", elf], check=True, capture_output=True, text=True).stdout
    for row in out.splitlines():
        parts = row.split()
        if len(parts) == 3 and parts[2] == name:
            return int(parts[0], 16)
    return None


def strip_spin(seq):
    """Drop crt0's final 'j 3b' loop: Spike only polls tohost every 5,000 instructions,
    so up to ~5,000 copies of it end the log and would inflate the instruction count."""
    end = len(seq)
    while end > 1 and seq[end - 1][0] == seq[-1][0]:
        end -= 1
    return seq[:end + 1] if end < len(seq) else seq   # keep one copy: it is the jump that was executed


def symbolize(elf, pcs):
    """Map pc -> (function, file:line) with addr2line."""
    if not pcs:
        return {}
    pcs = sorted(pcs)
    out = subprocess.run([PREFIX + "addr2line", "-f", "-C", "-e", elf] + [hex(p) for p in pcs],
                         check=True, capture_output=True, text=True).stdout.splitlines()
    res = {}
    for i, p in enumerate(pcs):
        fn, loc = out[2 * i], out[2 * i + 1]
        res[p] = (fn, os.path.basename(loc.split(" ")[0]))
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="C file (must define int main(void) and return)")
    ap.add_argument("-o", "--out", required=True, help="output folder")
    ap.add_argument("--opt", default="O1", help="optimisation level: O0, O1, O2, O3 or Os (O0 keeps every branch you wrote)")
    ap.add_argument("--march", default="rv32im", help="RISC-V ISA for gcc and Spike")
    ap.add_argument("--user-only", action="store_true",
                    help="keep only branches in the source file (drops crt0, mini_libc and libgcc)")
    ap.add_argument("--pc-bits", default="7:0", help="PC bit slice sent to the chip, MSB:LSB (704 design: 7:0; lab design: 9:2)")
    ap.add_argument("--timeout", type=int, default=120, help="seconds before Spike is stopped")
    ap.add_argument("--max-instructions", type=int, default=2_000_000,
                    help="Spike stops after this many instructions (a hung program costs ~53 bytes of log each)")
    a = ap.parse_args()

    # Compressed branches (c.beqz, c.bnez) are 2 bytes with a different opcode: they would vanish from the trace.
    exts = a.march.lower().split("_")[0][4:]          # "rv32imac_zicsr" -> "imac"
    if "c" in exts:
        sys.exit(f"error: --march {a.march} includes the C extension; its compressed branches are not traced. Use e.g. rv32im.")

    a.opt = "-" + a.opt.lstrip("-")           # accept both "O1" and "--opt=-O1"
    os.makedirs(a.out, exist_ok=True)
    name = os.path.splitext(os.path.basename(a.source))[0]
    elf = os.path.join(a.out, name + ".elf")
    log = os.path.join(a.out, "spike.log")

    compile_c(a.source, elf, a.opt, a.march)
    with open(os.path.join(a.out, name + ".dis"), "w") as f:
        run([PREFIX + "objdump", "-d", "-M", "no-aliases", elf], stdout=f)
    status = run_spike(elf, log, a.march, a.timeout, a.max_instructions)

    seq, log_lines, main_result = extract(log, symbol_addr(elf, "main_result"))
    if status is None:
        status = "timeout"                          # wall clock ran out
    elif status == 0 and log_lines >= a.max_instructions:
        status = "timeout"                          # instruction cap reached: main() never returned
    if status != 0:
        print(f"warning: Spike ended with status {status}; the trace is incomplete", file=sys.stderr)
        main_result = None                          # the only store seen was crt0 clearing .bss
    # keep only the user program (Spike's boot ROM lives at 0x1000), without crt0's final spin loop
    seq = [(pc, insn) for pc, insn in seq if pc >= 0x80000000]
    if status == 0:
        seq = strip_spin(seq)
    branches = []                                   # (pc, taken, insn)
    for i in range(len(seq) - 1):
        pc, insn = seq[i]
        if insn & 0x7F == BRANCH_OPCODE:
            branches.append((pc, int(seq[i + 1][0] != pc + 4), insn))

    syms = symbolize(elf, {pc for pc, _, _ in branches})
    if a.user_only:
        # by source file, not by function name: libgcc (division on rv32i, all float code) has
        # debug info too, e.g. "__hidden___udivsi3 div.S:88" or "__mulsf3 mulsf3.c:42"
        mine = os.path.basename(a.source)
        branches = [b for b in branches if syms.get(b[0], ("?", "?"))[1].rsplit(":", 1)[0] == mine]

    hi, lo = (int(x) for x in a.pc_bits.split(":"))
    mask = (1 << (hi - lo + 1)) - 1
    with open(os.path.join(a.out, "trace.txt"), "w") as t, \
         open(os.path.join(a.out, "trace.bin"), "wb") as b, \
         open(os.path.join(a.out, "tb_input.hex"), "w") as h:
        for pc, tk, _ in branches:
            t.write(f"{pc:08x} {tk}\n")
            b.write(struct.pack("<IB", pc, tk))
            h.write(f"{(pc >> lo) & mask:02x} {tk}\n")

    # Simulator-friendly copies of the same trace (no file I/O needed at run time):
    #   trace.mem      for $readmemh: one 3-digit hex word per branch = {taken, pc slice}
    #   trace_init.vh  for `include inside an initial block (works in browser simulators too)
    width = hi - lo + 1
    with open(os.path.join(a.out, "trace.mem"), "w") as m, \
         open(os.path.join(a.out, "trace_init.vh"), "w") as v:
        v.write(f"// generated by bptrace from {os.path.basename(a.source)}: {len(branches)} branches\n")
        v.write(f"N_BRANCHES = {len(branches)};\n")
        for i, (pc, tk, _) in enumerate(branches):
            word = (tk << width) | ((pc >> lo) & mask)
            m.write(f"{word:03x}\n")
            v.write(f"trace[{i}] = {width + 1}'h{word:03x};\n")

    per = collections.OrderedDict()
    for pc, tk, insn in branches:
        d = per.setdefault(pc, {"count": 0, "taken": 0, "transitions": 0, "last": None, "insn": insn})
        d["count"] += 1; d["taken"] += tk
        if d["last"] is not None and d["last"] != tk:
            d["transitions"] += 1
        d["last"] = tk
    with open(os.path.join(a.out, "branches.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pc", "count", "taken", "taken_rate", "transition_rate", "function", "line", "mnemonic"])
        for pc, d in sorted(per.items(), key=lambda kv: -kv[1]["count"]):
            fn, line = syms.get(pc, ("?", "?"))
            w.writerow([f"{pc:08x}", d["count"], d["taken"], round(d["taken"] / d["count"], 4),
                        round(d["transitions"] / max(1, d["count"] - 1), 4), fn, line,
                        FUNCT3.get((d["insn"] >> 12) & 7, "?")])

    summary = {"program": name, "opt": a.opt, "march": a.march, "spike_status": status,
               "instructions": len(seq), "branches": len(branches), "static_branches": len(per),
               "taken_rate": round(sum(t for _, t, _ in branches) / max(1, len(branches)), 4),
               "pc_bits": a.pc_bits,
               "main_result": main_result, "log_lines": log_lines, "max_instructions": a.max_instructions}
    json.dump(summary, open(os.path.join(a.out, "summary.json"), "w"), indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
