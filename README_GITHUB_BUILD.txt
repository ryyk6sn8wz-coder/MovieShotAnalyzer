Movie Shot Analyzer — Windows EXE 自動ビルド版

目的
-----
このプロジェクトは、GitHub Actions の Windows 環境で
MovieShotAnalyzer.exe を自動生成します。

ユーザーPCに Python は不要です。
GitHub Actions 側で Python と必要ライブラリを用意してビルドします。

最も簡単な使い方
----------------
1. GitHub に新しいリポジトリを作る
2. このフォルダの中身をアップロード
3. GitHub の「Actions」を開く
4. 「Build Windows EXE」を選択
5. 「Run workflow」を押す
6. 完了したら Actions の実行結果から
   「MovieShotAnalyzer-Windows」をダウンロード
7. ZIPを展開して MovieShotAnalyzer.exe をWindowsで起動

完成したEXEを使うだけなら、Windows側に Python は不要です。

注意
----
初回はGitHubへのアップロード作業が必要です。
また、Windows Defender等が初回起動時に確認を表示する場合があります。
