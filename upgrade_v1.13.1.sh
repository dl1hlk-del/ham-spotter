#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p backups data
chmod 700 backups 2>/dev/null || true
chmod +x hamspotter tools/hamspotter_manager.py 2>/dev/null || true
if [[ -f .env ]]; then
  cp -a .env "backups/.env.pre-v1.13.1-${STAMP}"
  chmod 600 "backups/.env.pre-v1.13.1-${STAMP}" 2>/dev/null || true
fi
echo "HAM Spotter V1.13.1 – Backup Reliability & Progress installed."
echo "Existing .env/data remain unchanged."
echo "Rebuild/restart with: docker compose up -d --build"
