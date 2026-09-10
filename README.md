# Movie Shot Analyzer V6.0.2 — Manual VP + Camera Info

- パース・レンズタブ上部の説明的な4項目を削除
- 「放射線を表示（補助）」マスター項目を削除
- VP1(X) + VP2(Z) が確定した時点で、以前の安定版と同様にX/Z放射線を自動表示
- VP3(Y)確定後はYも表示。∞判定時は平行ガイド
- 軸ごとのON/OFF、本数、色は維持
- 軸リセット後に引き直しても放射線表示が復帰
- Camera Solverは手動VPを動かさず、レンズ/FOV/Solve errorの評価に限定
- X=水平・左右、Y=垂直・上下、Z=奥行き表記
- 左右パネル開閉はFIX仕様として維持
- WindowsはPyInstaller --onefile
- GitHub ActionsのArtifactにはEXEを直接入れる（内側ZIPを作らない）
