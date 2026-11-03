#!/bin/bash
set -e

SKILL_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
exec python3 "$SKILL_DIR/scripts/explorer_server.py"
