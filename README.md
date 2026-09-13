# Movie Shot Analyzer v2.0.9 — Lens Reliability Gate

Based on v2.0.8 X/Z Axis Isolation.

## Fix in this build
- Added a reliability gate for artist-facing focal-length/FOV display.
- Low-confidence X/Z solutions are no longer shown as exact mm or FOV values.
- Prevents unstable geometry from being presented as plausible ultra-wide values such as 9–12 mm.
- Low-confidence cases now show:
  - 焦点距離：推定不可
  - レンズ域（参考）：判定困難
  - the instability reason
- Raw X/Z focal solution remains available only in the expandable diagnostic text and is explicitly marked as low-confidence / unused for display judgement.
- Medium/high-confidence results continue to show exact mm and H-FOV.
- Existing infinite-VP qualitative fallback remains unchanged.
- X/Z manual-only calculation policy and Y/VP3 isolation are unchanged.

## Safety behavior
This build does not clamp suspicious values to a preferred focal length. It rejects unstable numerical precision instead, so a genuine well-supported ultra-wide shot can still be reported if its X/Z solution is stable enough.
