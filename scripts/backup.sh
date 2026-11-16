#!/bin/sh
set -eu
ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT_DIR"
STAMP=$(date +%Y%m%d-%H%M%S)
mkdir -p backups
tar -czf "backups/deutsch-grader-$STAMP.tar.gz" data
echo "Backup written to backups/deutsch-grader-$STAMP.tar.gz"
