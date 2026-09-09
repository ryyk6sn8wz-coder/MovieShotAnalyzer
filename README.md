# Movie Shot Analyzer V5.16

V5.16 focuses on a larger image workspace and safer automatic perspective candidates.

## Changes
- Left / Right arrow keys move to previous / next image.
- F toggles an image-first view.
- Left and right panels are narrower and each can be collapsed independently.
- Existing `current / total` image count remains visible in the file information area.
- Automatic perspective search now keeps more diverse off-screen VP hypotheses instead of greedily consuming the first local line cluster.
- Long, frame-spanning architectural lines receive stronger weight; short local central edges receive lower weight.
- A/B/C candidate mode, manual correction, learning, lens estimation, perspective line width/opacity, and composition-guide opacity remain available.
- VP3 is still only adopted when vertical evidence is sufficiently strong.

## Windows
Run `run.bat` for source execution, or build with `build_exe.bat` / GitHub Actions.

## macOS
Run `build_macos.sh` on Apple Silicon or Intel macOS with Python installed.
