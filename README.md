# Movie Shot Analyzer macOS M4 build package v2.0.7

## 今回の修正
前の build_macos.command が Apple CommandLineTools 付属の Python 3.9 を拾い、pip の `encoding must be str, not None` で停止する問題を修正しました。

この版は:
- UTF-8環境を明示
- Python 3.11以上だけを使用
- Apple Silicon Homebrew の Python 3.12/3.13 を優先
- Homebrewがある場合は Python 3.12 を必要に応じて自動導入
- 依存パッケージは原則バイナリwheelを使用

## 使い方
1. ZIPを展開
2. `build_macos.command` をダブルクリック
3. 終了後、`dist/MovieShotAnalyzer.app` または `MovieShotAnalyzer.dmg` を使用

初回起動で止められた場合は Finder で app を右クリック → `開く` → `開く`。

## Pythonがないと言われた場合
Python 3.12を python.org からインストールしてから `build_macos.command` を再実行してください。
