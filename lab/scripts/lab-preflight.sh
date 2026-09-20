#!/usr/bin/env bash
set -u
fail=0
printf 'AMP lab preflight\n\n'
for cmd in docker curl python3; do
  if command -v "$cmd" >/dev/null 2>&1; then printf '[ok]   %-12s %s\n' "$cmd" "$(command -v "$cmd")"; else printf '[FAIL] %-12s missing\n' "$cmd"; fail=1; fi
done
if command -v docker >/dev/null 2>&1; then
  docker info >/dev/null 2>&1 && echo '[ok]   Docker daemon reachable' || { echo '[FAIL] Docker daemon is not reachable'; fail=1; }
  docker compose version 2>/dev/null || { echo '[FAIL] docker compose plugin missing'; fail=1; }
fi
printf '\nHost/WSL resources:\n'
printf '  CPUs:   %s\n' "$(nproc 2>/dev/null || echo unknown)"
printf '  Memory: %s\n' "$(free -h 2>/dev/null | awk '/^Mem:/{print $2}' || echo unknown)"
printf '  Disk:   %s free in %s\n' "$(df -h . 2>/dev/null | awk 'NR==2{print $4}' || echo unknown)" "$(pwd)"
printf '\nOptional Kubernetes tools:\n'
for cmd in kind kubectl helm; do
  if command -v "$cmd" >/dev/null 2>&1; then printf '[ok]   %-12s installed\n' "$cmd"; else printf '[info] %-12s not installed (only needed for Kubernetes lab)\n' "$cmd"; fi
done
exit "$fail"
