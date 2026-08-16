#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec bash ./upgrade_v1.13.5.sh
