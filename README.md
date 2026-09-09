# Movie Shot Analyzer V5.17

V5.17 fixes keyboard image navigation and makes automatic perspective analysis more robust.

- Left / Right arrow keys now navigate images even when the image canvas has focus.
- F toggles the image-priority view.
- A/B/C perspective candidates are filtered so near-identical candidates are not shown as separate choices.
- Automatic perspective analysis first uses strict architecture-priority detection, then falls back to a relaxed pass on sparse / low-contrast images.
- If only one reliable direction is available, the app shows that result instead of appearing unresponsive.
- Candidate state is reset when moving to a different image.
- Manual perspective, learning, lens estimate, guide opacity and line-width controls are preserved.
