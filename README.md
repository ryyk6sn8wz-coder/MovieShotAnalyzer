# Movie Shot Analyzer — Pure Manual Perspective v2

Pure Manual Perspective v2 は、手動で引いた2本の基準線だけからパースを扱う検証版です。

## v2 の変更
- 自動パース解析・自動候補・自動解析学習コードをパース系から完全に削除。
- X / Z / Y の全3軸で、弱い収束は無理に遠い有限VPを作らず「無限遠VP（平行方向）」として扱う。
- Y軸は建築の垂直線に対して少し保守的に∞判定し、誤ったVP3を作りにくくした。
- 無限遠判定された軸は、2本の手動基準線の平均方向から平行ガイドを生成。
- FIX表記：X軸（水平・左右方向） / Z軸（奥行き方向） / Y軸（垂直・上下方向）。
- FIX：パース・レンズタブ上部の説明文は表示しない。
- FIX：左右パネル開閉を維持。
- 構図ガイドは「基本ガイド」「考察ガイド」の区分を維持。
- 書き出しは表示中のパース線・構図ガイド・フレームを不透明度込みで反映。

## GitHub Actions
`.github/workflows/build-windows.yml` を含めてリポジトリへアップロードしてください。
Actions の Artifact は GitHub の仕様上 ZIP でダウンロードされますが、その中には `MovieShotAnalyzer.exe` が直接入ります。二重ZIPにはしません。
