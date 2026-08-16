#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p backups data
chmod 700 backups 2>/dev/null || true

if [[ -f .env ]]; then
  cp -a .env "backups/.env.pre-v1.13.2-${STAMP}"
  chmod 600 "backups/.env.pre-v1.13.2-${STAMP}" 2>/dev/null || true
  if ! grep -q '^HAMSPOTTER_LANGUAGE=' .env; then
    cat >> .env <<'EOF'

# HAM Spotter interface language
HAMSPOTTER_LANGUAGE=de
EOF
  fi
fi

chmod +x hamspotter install.sh tools/hamspotter_manager.py tools/hamspotter_manager_i18n.py 2>/dev/null || true

echo "HAM Spotter V1.13.2 – International Installer installed."
echo "Existing installations keep German as their management language unless changed with: hamspotter language en"
echo "Existing .env/data remain unchanged."
