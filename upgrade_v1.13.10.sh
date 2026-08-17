#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p backups data
chmod 700 backups 2>/dev/null || true
if [[ -f .env ]]; then
  cp -a .env "backups/.env.pre-v1.13.10-${STAMP}"
  chmod 600 "backups/.env.pre-v1.13.10-${STAMP}" 2>/dev/null || true
fi
chmod +x hamspotter install.sh tools/hamspotter_manager.py tools/hamspotter_manager_i18n.py upgrade*.sh 2>/dev/null || true

echo "HAM Spotter V1.13.10 – Reliability Hardening installed."
echo "RBN node refresh now rejects implausibly small partial snapshots and preserves the last known-good directory."
echo "CI/release validation now runs the complete test suite."
echo "Fresh installations default to 24 hours of raw spot retention."
echo "Existing .env/data remain unchanged."
