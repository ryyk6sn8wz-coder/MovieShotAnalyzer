# Movie Shot Analyzer V5.21

## V5.21
- 自動パースでは人物由来の短い線を原則低ウェイト化する土台を追加
- 窓・壁・床・天井・机列・道路など長い構造線を優先
- 信頼度の低いVPを無理に成立させない保守的方針を維持
- VP3は強い垂直収束がある場合のみ採用する方針を維持
- EYE LEVELは表示上、画面水平として扱う設計へ整理
- VP1–VP2の傾きは解析上のHorizonとしてEYE LEVELと分離する方針
- 手動で引いたVP1/VP2/VP3の2本の基準線を「線ファミリー教師データ」として保存できる構造を追加
- 自動解析→手動修正も教師データとして扱う
- 学習ON/OFF・履歴/リセットの既存機能を維持
- ←/→画像移動、F表示、左右パネル、線幅・透明度、A/B/C候補を維持

※ 人物の完全なセマンティックマスクには専用人物検出モデルが必要です。V5.21では追加の巨大AIモデルを同梱せず、
短線・中央局所線の抑制と構造線優先を強化する軽量方式を採用しています。

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
