#!/usr/bin/env bash
# Install the RISC-V toolchain used by this project.
# Tested on Ubuntu 24.04 (x86-64), 17 Oct 2026: GCC 13.2.0, Spike 1.1.1-dev @ 5a76066, 11 min build on 2 cores.
# Usage: bash setup_toolchain.sh            (installs Spike into $HOME/riscv-tools)
set -euo pipefail

PREFIX="${PREFIX:-$HOME/riscv-tools}"
SPIKE_COMMIT="${SPIKE_COMMIT:-5a760667bf0d0ab893173b9eeff9c109c8d7dd9a}"   # the commit tested; "master" for latest

. /etc/os-release
if [ "${VERSION_ID:-}" != "24.04" ]; then
  echo "WARNING: this is Ubuntu ${VERSION_ID:-?}. The team standard is 24.04 (GCC 13.2)."
  echo "         Other releases ship another GCC (22.04: 10.2, 25.x: 14.2, 26.04: 15.x), which moves"
  echo "         code addresses and changes the accuracy numbers. Use the shared machine for slide numbers."
fi

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  gcc-riscv64-unknown-elf binutils-riscv64-unknown-elf \
  device-tree-compiler build-essential git python3 python3-pip python3-venv cmake bc iverilog unzip

riscv64-unknown-elf-gcc --version | head -1
riscv64-unknown-elf-gcc -print-multi-lib | grep -q '^rv32im/ilp32;' \
  || { echo "ERROR: no rv32im/ilp32 multilib in this GCC"; exit 1; }

if [ ! -x "$PREFIX/bin/spike" ]; then
  src="$HOME/riscv-isa-sim"
  [ -d "$src" ] || git clone https://github.com/riscv-software-src/riscv-isa-sim.git "$src"
  git -C "$src" fetch --quiet origin && git -C "$src" checkout --quiet "$SPIKE_COMMIT"
  mkdir -p "$src/build" && cd "$src/build"
  ../configure --prefix="$PREFIX"
  make -j"$(nproc)"
  make install
fi

grep -q 'riscv-tools/bin' ~/.bashrc || echo "export PATH=$PREFIX/bin:\$PATH" >> ~/.bashrc
grep -q 'export SPIKE=' ~/.bashrc || echo "export SPIKE=$PREFIX/bin/spike" >> ~/.bashrc
echo "Done. Open a new terminal (or: source ~/.bashrc), then run: bash check_toolchain.sh"
