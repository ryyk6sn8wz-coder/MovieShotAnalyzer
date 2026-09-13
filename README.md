# Movie Shot Analyzer v2.0.7

## Changes
- VP3/Y axis is fully isolated from the X/Z camera/lens solution.
- Y/VP3 drag, anchor edits, reset, and helper-line refinement no longer recompute focal length or alter X/Z.
- Parallel/infinite/invalid Y results affect only Y/VP3; X/Z, eye level and lens state remain intact.
- When exact focal length cannot be solved, the app now shows an explicit qualitative lens region such as 標準域 / 中望遠域 / 中望遠〜望遠域 / 望遠域.
- Exact-mm estimates remain X+Z only; Y/VP3 is drawing perspective only.
- Keeps clickable lightweight thumbnail navigation and the existing v2.0.6 UI/analysis behavior.

## Build Windows
Use GitHub Actions or `build_exe.bat`.
