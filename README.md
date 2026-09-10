# Movie Shot Analyzer — Camera Calibration Solver v1.3

ベース: v1.2 Lens Fix

## v1.3
- H: 手のひらツールを固定ON/OFF
- Spaceを押している間: 一時的に手のひらツール
- 手のひら中の左ドラッグ: 画像ビューをパン
- パンは表示だけを移動し、パース座標・VP・レンズ計算は変更しない
- Ctrl+Z / Cmd+Z: 手動パースを1操作Undo
- Ctrl+Shift+Z / Cmd+Shift+Z: Redo
- X→Z→Yの途中でも、直前の線へ戻って引き直せる
- Undo/Redo後はCamera Solver・グリッド・レンズ表示を再計算
- レンズ欄に「旧方式比較」を追加
- 現在のX/Z独立推定、旧方式比較、Camera Solver側を比較可能
- v1.2のレンズ感度レンジを維持

## FIX
- パースCamera Solver本体の数式は変更していません
- パースグリッド間隔の計算は変更していません
- 自動パース判定データは使用しません
- __pycache__ は配布ZIPに含めません
