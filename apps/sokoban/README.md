# Sokoban

A turn-based box-pushing puzzle for SolarOS. Sokoban redraws only after the
board changes, making it especially comfortable on reflective and e-paper
displays. The board scales to the available graphic display.

Ten original, solver-verified levels focus on routing, box order, recovery
space, and avoiding attractive dead ends. Sol, the tiny warehouse cat, is a
transparent monochrome sprite with a drawing fallback for SolarOS <=4.6.7.

Controls:

- Arrow keys, WASD, or HJKL move.
- U or Backspace undoes one move.
- R restarts the current level.
- N, Page Down, or `]` opens the next level.
- P, Page Up, or `[` opens the previous level.
- Enter or Space advances after solving a level.
- Escape or Q exits.

Requires a graphic display.

Display rotation refits the current puzzle without resetting moves or undo history.
