#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -d .venv ]]; then
  echo ".venv がありません。先に build_macos.command を実行してください。"
  read -r -p "Enterで終了..." _
  exit 1
fi
source .venv/bin/activate
python movie_shot_analyzer.py
