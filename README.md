# Movie Shot Analyzer — v1.4 Perspective + Lens

Base: `CameraCalibrationSolver v1.3.2 Z Stable`

## v1.4 の方針
パース操作とレンズ解析を分離しました。

### Perspective Tool（作画用）
- X / Z / Y をそれぞれ2本の手動線から独立して解決
- 3軸を共有カメラ解で勝手に補正しない
- 確定した VP1 / VP2 / VP3 の色付きマーカーを直接ドラッグして微調整可能
- VPを動かすと、その軸の放射線・グリッドが追従
- Y / VP3 は作画用として独立。自分で引いた縦パースを優先
- Z補助線（最大6本）のロバスト推定を維持

### Lens Solver（fSpy型の基本2VP方式を参考）
- X + Z の現在の2消失点だけで焦点距離 / H-FOVを推定
- 主点は画像中央をデフォルト
- Y / VP3 はレンズ計算に一切入れない
- X/ZのVPを直接動かした場合はレンズ値も再計算
- YのVPを動かしてもレンズ値は変化しない
- 35mm換算、H-FOV、推定レンジ、レンズ傾向、信頼度を表示

## UI
- 右パネルをコンパクト化
- 軸ボタン: `X 水平 / Z 奥行 / Y 垂直`
- Z補助線を1行化
- グリッド本数操作をコンパクト化
- `カメラ / レンズ` を統合
- VP座標など診断情報は `詳細 ▼` に収納
- 横スクロールなし

## 維持した操作
- V5.30系の2ストローク鉛筆入力
- 1本目のロック保持
- Space: 一時手のひら
- H: 手のひら固定
- Ctrl/Cmd+Z: パースUndo
- Ctrl/Cmd+Shift+Z: Redo
- 自動パース判定データなし
- 黒帯自動検出 / 緑フレーム / 一括書き出し

## Windows EXE
GitHub Actions の `Build Windows EXE` を実行してください。
Artifact `MovieShotAnalyzer-Windows` の中には `MovieShotAnalyzer.exe` が直接入ります（二重ZIPにしません）。
