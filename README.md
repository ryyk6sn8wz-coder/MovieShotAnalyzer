# Movie Shot Analyzer v2.0 — Compact Refinement

## 今回の変更
- X / Z / Y の軸確定後は、画像上をそのまま追加ドラッグすると補助線になります。
  - 本数を先に指定する必要はありません。
  - 基本2本が誤って描き直される／リセットされる事故を防止します。
  - 各軸最大6本、自動カウント。
- X / Z / Y 補助線UIを1行に圧縮。
- X/Zのレンズ感度計算を「基本2本だけ」から「基本2本＋全補助線」のロバスト推定へ変更。
  - 各試行で全観測線を微小に揺らし、ロバストVPを再推定します。
  - 補助線を増やした時に推定値・感度範囲が安定するか確認できます。
- 推定不可時のレンズ感表示を狭め、極端に広い「広角〜中望遠」のような表現を避けます。
- パース・レンズ右パネルをコンパクト化し、通常表示では縦スクロールを使わない構成に変更。
- 左パネルも余白・ボタン・説明を圧縮し、スクロールバーを非表示化。
- 既存の4点編集、VP直接ドラッグ、Space/Hパン、Ctrl/Cmd+Z、VP3独立仕様は維持。

## レンズ計算の役割
- X + Z: 焦点距離 / H-FOV / レンズ感 / 信頼度
- Y / VP3: 作画用縦パース。レンズ値には影響しません。

## Windows EXE
GitHub Actions の `Build Windows EXE` を実行してください。Artifact内には `MovieShotAnalyzer.exe` が直接入ります。

## v2.0.1 UI Fix
- 前版 v2.0 をベースに機能構成を維持。
- 右側のXYZ/補助線表示をコンパクト化し、文字切れを抑制。
- 構図ガイドを3列配置にして縦方向を圧縮し、タブ内の縦スクロールを廃止。
- 左パネルの余白と説明文を整理し、縦スクロールなしで収まりやすく調整。
- 画面下にショット一覧サムネイルビューを追加。クリックで画像移動可能。

## v2.0.2 UI / Thumbnail Hotfix
- Fixed helper-count row clipping by shortening X/Z/Y helper labels and controls.
- Fixed severe slowdown introduced by the bottom shot view.
  - Thumbnails are no longer rebuilt/re-opened for every shot change.
  - Thumbnail creation is lazy and batched so the UI stays responsive.
  - Current-shot selection only updates button state.
- Perspective / lens calculation code is unchanged from v2.0.1.

## v2.0.3 — Thumbnail navigation
- Bottom filmstrip thumbnails are directly clickable.
- Clicking a thumbnail jumps to that exact image while preserving the current shot's frame/perspective state.
- The active thumbnail remains checked/highlighted and is automatically scrolled into view.
- Uses an explicit `shotIndex` property instead of lambda index capture for more robust navigation.

## v2.0.4 lightweight clickable thumbnail navigation
- Clicking a filmstrip thumbnail jumps directly to that image.
- Only the clicked source image is loaded into the Analyzer.
- The filmstrip no longer decodes all images in large folders.
- Thumbnail buttons are lightweight placeholders; only currently visible thumbnails are decoded.
- Visible thumbnail icons are cached and reused.
- Rapid horizontal scrolling is coalesced to avoid UI stalls.
- Existing per-shot frame/perspective state is saved/restored without rebuilding the filmstrip.
