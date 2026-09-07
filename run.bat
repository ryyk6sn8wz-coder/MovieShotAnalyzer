@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt
python movie_shot_analyzer.py
pause
