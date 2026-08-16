#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec ./upgrade_v1.13.1.sh
