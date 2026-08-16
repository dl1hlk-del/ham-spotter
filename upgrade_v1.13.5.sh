#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p backups data
chmod 700 backups 2>/dev/null || true
if [[ -f .env ]]; then
  cp -a .env "backups/.env.pre-v1.13.5-${STAMP}"
  chmod 600 "backups/.env.pre-v1.13.5-${STAMP}" 2>/dev/null || true
fi
chmod +x hamspotter install.sh tools/hamspotter_manager.py tools/hamspotter_manager_i18n.py upgrade*.sh 2>/dev/null || true
echo "HAM Spotter V1.13.5 – updater permission fix installed."
echo "Upgrade helpers are now permission-safe after ZIP extraction."
echo "Existing .env/data remain unchanged."
