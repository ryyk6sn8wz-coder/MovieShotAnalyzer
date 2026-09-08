# Movie Shot Analyzer V4 Base

This is the stability-check build before advanced Analyzer features are added.

## What to verify on Windows
1. The left control panel is visible (not an empty white area).
2. `画像を開く` loads an image.
3. `フォルダを開く` loads all supported images recursively.
4. Drag & drop of an image or folder works.
5. `三分割を表示` toggles red thirds lines.
6. `中央十字を表示` toggles blue center lines.
7. Previous/next buttons move through a loaded folder.

Advanced perspective, VP, frame detection and lens-estimation features are intentionally NOT included in this base build. They will be added only after the Windows EXE UI is confirmed stable.

## GitHub Actions
Run workflow: `Build Movie Shot Analyzer V4 Base for Windows`
Download artifact: `MovieShotAnalyzer-V4-Base-Windows`
