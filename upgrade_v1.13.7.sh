#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p backups data
chmod 700 backups 2>/dev/null || true
if [[ -f .env ]]; then
  cp -a .env "backups/.env.pre-v1.13.7-${STAMP}"
  chmod 600 "backups/.env.pre-v1.13.7-${STAMP}" 2>/dev/null || true
fi
chmod +x hamspotter install.sh tools/hamspotter_manager.py tools/hamspotter_manager_i18n.py upgrade*.sh 2>/dev/null || true
echo "HAM Spotter V1.13.7 – self-update handoff fix installed."
echo "Future updates hand control to the freshly installed manager before restart/health verification."
echo "Existing .env/data remain unchanged."
