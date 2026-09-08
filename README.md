# Movie Shot Analyzer V5.1 — Composition Guides

Windows / macOS (Apple Silicon) 共通ソースの構図分析ツールです。

## V5.1 追加ガイド
既存の三分割、中央十字、黄金比、黄金螺旋、対角線、三角構図、対称軸に加えて、以下を追加しています。

- 放射構図 (Radiating)
- トンネル / フレームインフレーム (Tunnel / Frame in Frame)
- ゴールデントライアングル (Golden Triangle)
- 円構図 (Circular)
- C字構図 (C-shape)
- V字構図 (V-shape)
- ダブル対角線 (Double Diagonal)
- S字構図 (S-curve)
- L字構図 (L-shape)
- ピラミッド構図 (Pyramid)

Balance / Unbalanced のように「被写体の視覚重量」を見ないと判断できないものは固定ガイドにはせず、後の画像解析による構図タイプ判定へ回す設計です。

## 維持している機能
- Photoshop風の実映像フレーム自由変形
- 黒帯自動検出
- 補助線（縦 / 横 / 自由線）
- ガイド線太さ 0.5刻み
- 交点は外周なしの塗りつぶし円
- ガイド / ポイント / フレーム透明度 0% で完全非表示
- 明るさ / コントラスト / ガンマ / 彩度（表示のみ）
- 画像 / フォルダのドラッグ&ドロップ
- Windows / macOS Apple Silicon 共通ソース

## Windows
`build_exe.bat` または GitHub Actions + PyInstaller でビルドできます。

## macOS Apple Silicon
`build_macos.sh` を用意しています。M4 Maxを含むApple Siliconを対象にしています。
