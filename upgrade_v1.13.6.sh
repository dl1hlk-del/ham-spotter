#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p backups data
chmod 700 backups 2>/dev/null || true
if [[ -f .env ]]; then
  cp -a .env "backups/.env.pre-v1.13.6-${STAMP}"
  chmod 600 "backups/.env.pre-v1.13.6-${STAMP}" 2>/dev/null || true
fi
chmod +x hamspotter install.sh tools/hamspotter_manager.py tools/hamspotter_manager_i18n.py upgrade*.sh 2>/dev/null || true
echo "HAM Spotter V1.13.6 – startup health-wait reliability fix installed."
echo "The manager now waits for Docker health and the API before reporting restart/update success."
echo "Existing .env/data remain unchanged."
