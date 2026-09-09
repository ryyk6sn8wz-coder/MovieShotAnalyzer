# Movie Shot Analyzer V5.18

V5.18 changes automatic perspective analysis from "intersection candidates first" to **direction-cluster first**.

## Main changes
- Detect dominant architectural line directions before solving vanishing points.
- Robust weighted least-squares VP solve for each direction family.
- Long / frame-spanning structural lines receive stronger weight; short central object/person edges receive less weight.
- A/B/C are generated from different direction-family combinations instead of near-duplicate VP intersections.
- Near-vertical direction families are treated primarily as VP3 validation and are not forced.
- Far off-screen vanishing points are allowed.
- Falls back to the previous relaxed solver only if direction clustering cannot form a usable candidate.
- Keyboard Left/Right image navigation and the V5.17 viewer/UI improvements are retained.

Build as before with GitHub Actions or `build_exe.bat`.
