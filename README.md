# Movie Shot Analyzer v2.0.5 — XYZ Label / Thumbnail Jump Fix

Based on v2.0.4.

## Fixes
- Perspective grid rows now use a separate checkbox and X/Z/Y label so axis letters do not get clipped by the checkbox indicator.
- Bottom thumbnail buttons capture their shot index directly when connected. This avoids the Windows/PySide6 packaged-build issue where `sender()` / dynamic properties could fail and clicks would not navigate.
- Thumbnail navigation remains lightweight: only the clicked source image is loaded; visible thumbnail icons are still lazy-loaded and cached.
- Perspective/lens solver behavior is unchanged.
