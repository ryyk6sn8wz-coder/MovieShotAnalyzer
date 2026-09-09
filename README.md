# Movie Shot Analyzer V5.20

## V5.20 changes
- 自動パースを「方向ファミリー」中心に整理
- 建築・窓・机など、同じ3D方向に属する線群を先にまとめてからVP候補を評価
- ほぼ平行な縦線はVP3を無限遠として扱い、無理な3点透視を抑制
- 画像内の局所的なVP（人物や短線密集由来）を減点
- A/B/C候補は方向差を重視
- 自動解析時の放射線初期本数を8本に抑制
- 手動・修正パース学習は維持し、方向ファミリー選択の補助に利用
- ← / → キー、Fキー、左右パネル折りたたみ等は維持

# Movie Shot Analyzer V5.19

V5.19 focuses on reducing false automatic perspective results instead of forcing a VP solution on every frame.

## Main changes

- Stronger suppression of short/local line clusters around the central subject area (people, clothing, props, small desk edges).
- Stronger preference for long, spatially distributed architectural lines.
- Larger minimum angle separation between the main direction families.
- A weak 2-direction solution is now rejected instead of being displayed as a plausible-looking grid.
- If only one direction is reliable, the analyzer stops at **1 direction** and reports that the second direction is undecided.
- If no reliable direction exists, the UI explicitly reports **判定不能 / 有効なパース方向を検出できません**.
- VP3 requires stronger, more widely distributed vertical evidence.
- Learned manual/corrected line angles are used as a soft ranking preference for otherwise plausible direction clusters.
- The old free-intersection fallback is disabled in automatic analysis so it cannot manufacture a false VP pair.
- Existing keyboard navigation, wide viewer, manual perspective workflow, learning, line width/opacity and composition controls are preserved.

## Build

Use Python 3.12. Install `requirements.txt`, then run PyInstaller as before. The included GitHub Actions-compatible files remain unchanged in structure.
