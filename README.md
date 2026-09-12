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

## v1.5 - Editable VP guide endpoints
- After the second calibration line appears, the first line's two white endpoint handles remain visible and editable.
- Once an axis is solved, all four endpoint handles (line 1 + line 2) remain editable whenever that X/Z/Y axis is selected.
- Dragging any of the four endpoints updates the vanishing point in real time.
- Editing line 1 no longer resets or erases line 2.
- The line-1 safety copy is updated after edits, preventing an older line 1 from being restored accidentally.
- Solved VP markers remain directly draggable.
- Lens architecture remains unchanged: X+Z drive lens/FOV; Y/VP3 is an independent drawing-perspective guide.

## v1.6 Lens Stability / Integrated UI
- Removed the separate 「ショット分析」 tab; perspective and lens analysis now live together in 「パース・レンズ」.
- Lens confidence now tests the *drawn X/Z guide angles*, not just a few-pixel VP jitter.
- Near-parallel guide pairs / very distant VPs are automatically downgraded to low confidence.
- Low-confidence results show a warning and a sensitivity range rather than presenting a narrow high-confidence range.
- Y/VP3 remains independent drawing perspective and never changes lens/FOV.
- v1.5 four-point editable calibration and draggable VP behavior are preserved.

## v1.8 Integrated Shot Analysis
- 「ショット分析」を独立タブに戻さず、「パース・レンズ > カメラ / レンズ」に統合。
- X/Zのレンズ/FOVから、作画向けの「レンズ感・パース感・圧縮感」を同時表示。
- 焦点距離が推定不可でも、v1.7のレンズ感フォールバックをショット分析へ引き継ぐ。
- CU/MS/LS等のショットサイズは焦点距離だけでは決められないため、現段階では誤判定せず別判定と明示。
- Y/VP3は従来どおり作画用として独立し、レンズ値には影響しない。

## v1.9 Multi-Axis Refinement
- X / Z / Y の各軸に最大6本の補助線を追加可能。
- 各軸の基本2本＋補助線をロバスト最小二乗でまとめ、外れた線の影響を抑えてVPを再推定。
- レンズ推定は従来どおり X + Z のみ。Y補助線はVP3/作画グリッド安定化専用でレンズ値を変更しない。
- 補助線は軸色の破線で表示。各軸ごとに追加・クリア可能。
- Y軸を含む2本目の基準線ドラッグ中は重いVP/グリッド再計算を行わず、マウスリリース時に一度だけ計算。描画のカクつきを軽減。
- 既存の手描き2ストローク、確定後の4端点編集、VPドラッグ、Undo、Space/Hパンを維持。
