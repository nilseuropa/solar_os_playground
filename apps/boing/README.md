# Boing

An Amiga-inspired bouncing-ball demo rendered by the native Jet 3D engine.
One spinning red and white checkered sphere bounces above a perspective floor
grid, with a translucent shadow that follows its height. The floor, back wall,
and both side walls use the terminal foreground color; the background uses
the terminal background color. The ball's colors stay literal red and white.
Floor and wall impacts trigger short synthesized boings, with small pitch and
glide variations. Sounds use the global speaker volume.

Launch **Boing** from Playground on a display. The scene fits the display's
dimensions and needs no external assets.

Controls:

- **Space** or **P**: pause or resume.
- **R**: restart the bounce.
- **M**: mute or unmute the boing sounds.
- **Q**, **Escape**, or the system app-exit key (**Ctrl+]**): exit.

Requires Lua, the Jet firmware service, PSRAM, and a graphics display. Install
the Jet resident library (1.11 or newer) with `pkg install jet`. Color TFTs are
recommended for animation; monochrome displays dither the scene and e-paper
refresh limits apply.

Sound uses the optional `solaros.synth` service and an audio output. If the
service is absent, occupied, or cannot start, the visual demo continues silently.
Pause, restart, and mute release any playing notes; exiting releases the synth.

Firmware with `solaros.gfx.theme()` reads the terminal colors at launch.
On firmware without this helper, pass both colors explicitly:

```text
boing --foreground ff9000 --background 220000
```

The optional `--foreground RRGGBB` and `--background RRGGBB` arguments also
override the theme on newer firmware. No terminal settings are changed.

The example shows a reusable indexed checker texture, a sphere primitive,
a cached 3D room, an alpha-blended shadow, and cooperative animation paced at
up to 20 frames per second. Collisions include a contact frame at the floor
and each side wall. The room's color and depth are cached once, so its tiled
surfaces do not need to be rendered again during animation. All scene resources
are released on exit or an error.
