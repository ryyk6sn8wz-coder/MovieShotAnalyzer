# Movie Shot Analyzer — Camera Calibration Solver v1.3.1 VP3 Stable

v1.3 の操作系・レンズ比較を維持した VP3 安定化版です。

## 修正
- X軸 + Z軸が確定した時点のカメラ解をコアとして固定
- VP3/Y軸を確定しても X/Z の消失点・グリッド・焦点距離を再最適化しない
- VP3 は Y軸の向き（符号）選択と Solve error の検証に使用
- これにより VP3 確定直後に X/Z グリッド全体が大きく回転・崩れる現象を防止

## 維持
- Space: 一時手のひら
- H: 手のひら固定
- Ctrl/Cmd+Z: パースUndo
- Ctrl/Cmd+Shift+Z: Redo
- X/Z raw・旧方式・Camera Solver のレンズ比較
- 自動パース判定データなし
