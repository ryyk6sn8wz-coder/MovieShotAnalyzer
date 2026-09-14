# Movie Shot Analyzer v2.0.10 — Background Batch Export

Based on v2.0.9 Lens Reliability Gate.

## Main fix
- `全画像を一括書き出し` now runs in a **separate background process**.
- The main Movie Shot Analyzer window no longer cycles through every image during export.
- You can keep changing shots, zooming, drawing guides, and using the app while the batch export continues.
- Export progress is reported back to the main window.
- The existing cancel button terminates the background exporter without closing the main app.

## Why this changes the previous behavior
v2.0.9 moved PNG file compression/writing to worker threads, but image loading and canvas rendering still ran on Qt's GUI thread. That meant a large batch could still monopolize the interface and make the app appear frozen.

v2.0.10 takes a snapshot of the export state and launches a second Movie Shot Analyzer process dedicated to rendering and saving. The normal app UI is therefore not used by the export loop.

## Export snapshot
At the moment `全画像を一括書き出し` is pressed, the exporter snapshots:
- source image list
- frame transforms
- saved perspective data for each image
- composition-guide visibility and positions
- helper lines
- guide/frame/perspective colors, widths and opacity
- brightness / contrast / gamma / saturation

Edits made **after** the batch starts do not change that batch. This is intentional so the user can continue working safely while export is running.

## Existing v2.0.9 behavior retained
- X/Z manual-only lens calculation
- Y/VP3 isolation
- Lens Reliability Gate
- Low-confidence focal-length/FOV suppression
- Infinite-VP qualitative fallback
