@echo off
setlocal
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean --onefile --windowed --name MovieShotAnalyzer movie_shot_analyzer.py
echo.
echo Build complete: dist\MovieShotAnalyzer.exe
pause
