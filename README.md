# Movie Shot Analyzer v2.0.8

## Lens calibration change: FOV first
This version does **not** add an Ozu/Tokyo Story-specific correction.
The X/Z vanishing-point geometry continues to determine focal length in pixels and H-FOV exactly as before.

The important change is that the UI now separates:
- **H-FOV**: geometry-derived value that does not depend on sensor/film width.
- **35mm-equivalent focal length**: normalized to a 36mm-wide full-frame reference.
- **Capture-format focal length**: optional conversion when the acquisition aperture/sensor width is known.

## Capture-format presets
- 35mm equivalent / Full Frame: 36mm width (default)
- Normal 35 Academy / N35: 22mm width
- Super 35 DIN: 24mm width
- Super 35 ANSI: 24.9mm width
- Normal 16: 10.3mm width
- Super 16: 12.35mm width

The default remains 35mm equivalent so analyses across different movies stay comparable.
Only select a capture format when it is known or strongly documented.

## Why this matters
A physical 50mm lens on Normal 35 Academy (22mm image width) has a much narrower horizontal field of view than a 50mm lens on a 36mm-wide full-frame sensor. Comparing a documented cinema-lens focal length directly with a 35mm-equivalent result can therefore look like a large error even when the FOV is much closer.

## Preserved from v2.0.7
- VP3/Y fully isolated from X/Z camera/lens solution.
- Parallel/invalid Y cannot corrupt X/Z, eye level or lens state.
- Qualitative lens-region fallback when exact mm cannot be solved.
- X/Z auxiliary refinement lines.
- Clickable lightweight thumbnail navigation.
- Stable manual pencil workflow.

## Build Windows
Use GitHub Actions or `build_exe.bat`.
