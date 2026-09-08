@echo off
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean --windowed --onedir --name MovieShotAnalyzer movie_shot_analyzer.py
pause
