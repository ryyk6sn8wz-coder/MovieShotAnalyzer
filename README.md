# Movie Shot Analyzer v2.0.6

Thumbnail jump hotfix based on v2.0.5.

- Fixed thumbnail click navigation: the old handler called two non-existent methods, so only the blue checked frame changed and the central image never moved.
- Thumbnail click now uses the same `save_frame()` + `save_perspective()` path as Previous / Next.
- Clicking a thumbnail loads only the selected source image; the filmstrip is not rebuilt.
- Existing lazy thumbnail cache, perspective, lens, and XYZ UI behavior are otherwise unchanged.
