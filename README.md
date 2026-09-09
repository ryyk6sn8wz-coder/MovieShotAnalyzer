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
