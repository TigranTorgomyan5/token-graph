#!/bin/bash
set -e

PROJECT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
exec python3 "$PROJECT_DIR/token-graph/scripts/explorer_server.py"
