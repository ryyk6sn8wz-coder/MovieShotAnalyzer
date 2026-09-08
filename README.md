# Movie Shot Analyzer V5

Windows / macOS (Apple Silicon) 共通ソースの構図分析ツールです。

## V5 重点変更
- 実映像フレームをPhotoshop風の自由変形操作に変更
  - 四隅: X/Y自由移動（斜め可）
  - 辺中央: 選択した辺だけを辺に垂直な方向へ移動
  - 枠内: フレーム全体を移動
- 変形ハンドルを大きくして操作しやすく改善
- フレーム色・太さ（0.5刻み）・透明度を追加
- 交点ポイントは外周線なしの塗りつぶし円のみ
- 交点ポイントはサイズ（0.5刻み）・色・透明度を調整
- ガイド線は0.5刻み、透明度0%で完全非表示
- 補助線はフレーム自由変形ONでも直接ドラッグ可能
- 黒帯自動検出、明るさ/コントラスト/ガンマ/彩度を維持

## Windows
`build_exe.bat` または GitHub Actions + PyInstaller でビルドできます。

## macOS Apple Silicon
`build_macos.sh` を用意しています。最終版ではM4 Maxを含むApple Siliconを正式対象にします。
