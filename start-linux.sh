#!/usr/bin/env bash
# Start the NERVIS ecosystem:  ./start-linux.sh
set -euo pipefail
cd "$(dirname "$0")"
exec python3 tools/run.py start
