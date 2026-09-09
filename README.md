# Movie Shot Analyzer V5.5

V5.5 adds a VanishPoint-style perspective calibration workflow.

## Perspective calibration
- VP1 / VP2 / VP3 supported.
- Each vanishing direction uses **two calibration lines**.
- Each calibration line has **exactly two white anchor points**.
- Drag the two anchors so the line lies on a real edge in the image.
- The intersection of the two calibration lines is solved automatically as the VP.
- VP1 + VP2 automatically define the horizon / eye-level line.
- VP3 is solved independently for vertical convergence.
- Colored VP markers are results; normal calibration is performed with the white anchors.
- The green movie-frame transform is locked by default to prevent accidental movement.

## View navigation
- Mouse wheel zooms the canvas view from 25% to 400%.
- `100%に戻す` resets wheel zoom.
- The existing workspace scale remains available to create extra room for off-image vanishing points.

## Existing features retained
- Basic composition guides and filled intersection points.
- Movable additional composition guides.
- Tunnel guide with 8 edit handles.
- Actual movie-frame / black-bar detection.
- Display-only brightness, contrast, gamma and saturation correction.

## Windows build
Use the existing GitHub Actions workflow. Upload/overwrite the six repository files, commit, then run the workflow.
