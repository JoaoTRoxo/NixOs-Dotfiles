#!/usr/bin/env bash
# Report x86 instructions that a Core 2 Quad Q6600 (Kentsfield: SSE..SSSE3) can't run.
#
#   core2-isa-check.sh [--strict] <elf>...
#
# --strict exits 1 if any file contains such instructions. Only use it on code
# without runtime CPU dispatch (e.g. SQLite): ffmpeg, Skia, x265... ship AVX
# paths on purpose and only take them after checking CPUID.
set -euo pipefail

strict=0
if [ "${1:-}" = --strict ]; then strict=1; shift; fi

bad=0
for f in "$@"; do
  report=$(objdump -d --no-show-raw-insn "$f" | awk '
    $1 ~ /^[0-9a-f]+:$/ {
      m = $2
      if (m ~ /^(blendv?p[sd]|dpp[sd]|extractps|insertps|movntdqa|mpsadbw|packusdw|pblendvb|pblendw|pcmpeqq|pextr[bdq]|pinsr[bdq]|phminposuw|pmaxs[bd]|pmaxu[dw]|pmins[bd]|pminu[dw]|pmov[sz]x[bwd][wdq]|pmuldq|pmulld|ptest|round[ps][sd])$/) c["sse4.1"]++
      else if (m ~ /^(pcmp[ei]str[im]|pcmpgtq|crc32[bwlq]?)$/) c["sse4.2"]++
      else if (m ~ /^popcnt/) c["popcnt"]++
      else if (m ~ /^lzcnt/) c["lzcnt"]++
      else if (m ~ /^(andn|bextr|blsi|blsmsk|blsr|bzhi|mulx|pdep|pext|rorx|sarx|shlx|shrx)$/) c["bmi"]++
      else if (m ~ /^movbe/) c["movbe"]++
      else if (m ~ /^v/ && m !~ /^ver[rw]/) c[($0 ~ /zmm|%k[0-7]/) ? "avx512" : "avx"]++
    }
    END { for (k in c) printf " %s=%d", k, c[k] }')
  echo "$(basename "$f"):${report:- clean}"
  if [ -n "$report" ]; then bad=1; fi
done

if [ "$strict" = 1 ] && [ "$bad" = 1 ]; then
  echo "error: found instructions this CPU doesn't have" >&2
  exit 1
fi
