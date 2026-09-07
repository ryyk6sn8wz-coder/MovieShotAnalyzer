@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt
python -m pip install pyinstaller
pyinstaller --noconfirm --clean --onefile --windowed --name MovieShotAnalyzer movie_shot_analyzer.py
echo.
echo EXE: dist\MovieShotAnalyzer.exe
pause
