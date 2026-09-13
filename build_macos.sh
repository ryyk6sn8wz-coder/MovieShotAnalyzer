#!/bin/bash
set -e
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt pyinstaller
python3 -m PyInstaller --noconfirm --clean --windowed --onedir --name MovieShotAnalyzer movie_shot_analyzer.py
echo "Built: dist/MovieShotAnalyzer.app"
