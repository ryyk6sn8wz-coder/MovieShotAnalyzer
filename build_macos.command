#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

# Avoid the encoding=None bug of Apple's CommandLineTools Python/pip.
export LANG="${LANG:-en_US.UTF-8}"
export LC_ALL="${LC_ALL:-en_US.UTF-8}"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

pause_exit() {
  echo
  read -r -p "Enterで終了..." _ || true
  exit "${1:-1}"
}

version_ok() {
  "$1" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
}

find_python() {
  local c
  for c in \
    /opt/homebrew/bin/python3.13 \
    /opt/homebrew/bin/python3.12 \
    /opt/homebrew/bin/python3.11 \
    /opt/homebrew/bin/python3 \
    /usr/local/bin/python3.13 \
    /usr/local/bin/python3.12 \
    /usr/local/bin/python3.11 \
    /usr/local/bin/python3 \
    python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1; then
      local p
      p="$(command -v "$c")"
      if version_ok "$p"; then
        echo "$p"
        return 0
      fi
    fi
  done
  return 1
}

echo "=== Movie Shot Analyzer macOS build v2.0.7 ==="
echo "Machine: $(uname -m)"
echo "macOS: $(sw_vers -productVersion)"

if [[ "$(uname -m)" != "arm64" ]]; then
  echo "WARNING: Apple Siliconではありません。現在のCPU向けにビルドします。"
fi

PYTHON_BIN="$(find_python || true)"

if [[ -z "$PYTHON_BIN" ]]; then
  echo
  echo "Python 3.11以上が見つかりません。"
  echo "現在Macが選んでいるPythonは次の可能性があります:"
  command -v python3 || true
  python3 --version 2>/dev/null || true
  echo
  if command -v brew >/dev/null 2>&1; then
    echo "Homebrewが見つかったので Python 3.12 を自動インストールします。"
    brew install python@3.12
    PYTHON_BIN="/opt/homebrew/bin/python3.12"
    if [[ ! -x "$PYTHON_BIN" ]]; then
      PYTHON_BIN="$(find_python || true)"
    fi
  fi
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo
  echo "ビルドを停止しました。AppleのCommandLineTools Python 3.9は使用しません。"
  echo "Python 3.12を入れてから、この build_macos.command をもう一度ダブルクリックしてください。"
  echo "おすすめ: https://www.python.org/downloads/macos/"
  pause_exit 1
fi

echo "使用Python: $PYTHON_BIN"
"$PYTHON_BIN" --version

rm -rf .venv build dist MovieShotAnalyzer.spec MovieShotAnalyzer.dmg dmg-root
"$PYTHON_BIN" -m venv .venv
source .venv/bin/activate

# Use the venv's own Python explicitly and keep UTF-8 forced.
python -m pip install --upgrade pip setuptools wheel
python -m pip install --only-binary=:all: -r requirements.txt
python -m pip install pyinstaller

python -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --onedir \
  --name MovieShotAnalyzer \
  movie_shot_analyzer.py

APP="dist/MovieShotAnalyzer.app"
if [[ ! -d "$APP" ]]; then
  echo "ビルド失敗: $APP がありません。"
  pause_exit 1
fi

codesign --force --deep --sign - "$APP"

mkdir -p dmg-root
cp -R "$APP" dmg-root/
ln -s /Applications dmg-root/Applications
hdiutil create \
  -volname "Movie Shot Analyzer" \
  -srcfolder dmg-root \
  -ov \
  -format UDZO \
  MovieShotAnalyzer.dmg
rm -rf dmg-root

echo
echo "=== 完了 ==="
echo "App: $(pwd)/$APP"
echo "DMG: $(pwd)/MovieShotAnalyzer.dmg"
echo
echo "まず dist の MovieShotAnalyzer.app を右クリック → 開く で試してください。"
pause_exit 0
