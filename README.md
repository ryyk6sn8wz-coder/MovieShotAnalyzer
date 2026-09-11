# Movie Shot Analyzer — Camera Calibration Solver v1.3.2 Z Stable

Base: v1.3.1 VP3 Stable

## Main fixes
- 1本目のパース線を独立コピーでロック保持
- 2本目開始時に1本目を必ず復元するため、1本目が消える/上書きされる症状を防止
- Z軸（奥行き）に追加補助線を最大6本追加可能
- Z軸は基本2本 + 補助線をHomogeneous Least Squares + IRLSでロバスト推定
- 外れ気味の補助線の重みを自動的に下げる
- Z補助線は元の2本を置換せず、観測を追加するだけ
- レンズ推定側もZ補助線を含む同じVP2を使用

## Preserved
- v1.3.1のVP3 Stable仕様
- Space: 一時手のひら
- H: 手のひら固定
- Ctrl/Cmd+Z: パースUndo
- Ctrl/Cmd+Shift+Z: Redo
- 自動パース判定データなし

## Comparison references
問題が出る場合は以下と比較:
- MovieShotAnalyzer_CameraCalibrationSolver_v1_3_1_VP3Stable_GitHub.zip
- MovieShotAnalyzer_CameraCalibrationSolver_v1_3_HandUndo_LensCompare_GitHub.zip
- 手動2本入力の基準挙動: MovieShotAnalyzer_V5_30_RestoredV5_11ManualPerspective_GitHub.zip

## Z補助線の使い方
1. X軸とZ軸を通常どおり2本ずつ確定
2. 「Z補助線を追加」を押す
3. 机・窓枠・床・棚など、同じ奥行き方向のエッジを1本ドラッグ
4. 必要なら2〜6本追加
5. VP2 / Camera Solver / レンズ推定がその都度再計算
