# Movie Shot Analyzer v2.0.8 — X/Z Axis Isolation

Based on v2.0.7.

## Fix in this build
- Added a transaction guard between X/VP1 and Z/VP2.
- Confirming/re-solving VP2 restores and preserves the complete VP1 state before lens calculation.
- Confirming/re-solving VP1 likewise preserves VP2.
- Preserved base lines, auxiliary lines, completion state, infinity state/direction, locked line 1, and edit-anchor state.
- Lens calculation may read X+Z but no longer owns or rewrites either axis.
- VP3/Y isolation from v2.0.7 remains unchanged.
- Stable manual pencil workflow and thumbnail navigation are unchanged.

This is a defensive bug-fix build. It does not add Ozu-specific lens correction.
