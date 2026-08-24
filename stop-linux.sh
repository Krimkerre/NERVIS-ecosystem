#!/usr/bin/env bash
# Stop the NERVIS ecosystem:  ./stop-linux.sh
set -euo pipefail
cd "$(dirname "$0")"
exec python3 tools/run.py stop
