# Movie Shot Analyzer — Camera Calibration Solver v1.2 Lens Fix

パース精度・グリッド計算には触れず、レンズ推定だけを分離・修正した版です。

## レンズ推定
- 主推定：手動X軸VP × 手動Z軸VP
- Camera Solver由来の焦点距離は比較用に限定
- Y/VP3はレンズ中心値を引っ張らない
- 約4px相当の入力揺らぎをモンテカルロ評価し、10–90%レンジを推定
- レンズ信頼度はこの感度レンジから判定
- Solve error（パース整合度）とレンズ信頼度を分離
- 38mm固定のようなCamera Solver側への吸着をレンズ主値から排除

## パース
Camera Calibration Solver v1.1 のパース計算・グリッド間隔・XYZ解は変更していません。

## FIX
- 左右パネル開閉を維持
- パースタブ上部に説明文なし
- X軸（水平・左右方向）
- Z軸（奥行き方向）
- Y軸（垂直・上下方向）
- 自動判定データは使用しない
