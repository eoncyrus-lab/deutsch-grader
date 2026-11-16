#!/bin/sh
set -eu
ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TARGET="$HOME/.agents/skills/deutsch-grader"
mkdir -p "$(dirname "$TARGET")"
cp -R "$ROOT_DIR/.agents/skills/deutsch-grader/." "$TARGET/"
chmod +x "$TARGET/scripts/grade.py"
echo "Installed Cindy Skill: $TARGET"
echo "Start a new Cindy session to load it."
