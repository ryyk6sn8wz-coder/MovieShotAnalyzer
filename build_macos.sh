#!/bin/zsh
set -e
cd "$(dirname "$0")"
python3 -m pip install -r requirements.txt pyinstaller
python3 -m PyInstaller --noconfirm --clean --windowed --name MovieShotAnalyzer movie_shot_analyzer.py
echo "Build finished: dist/MovieShotAnalyzer.app"
