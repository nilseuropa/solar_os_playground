# Hex-O-Spell

Hex-O-Spell turns a SolarOS gadget into an accessible BLE keyboard. Its
two-stage radial selector provides the alphabet, period, question mark, space,
and backspace with six directions and one trigger.

Select a group and trigger it, then select and trigger characters. The group
stays open for consecutive characters; select `<` to return to the group ring.
Letters are sent as uppercase characters, matching the original interaction.

## Pairing

The first run shows a pairing popup before BLE advertising starts. Tap **Pair**
or trigger it with Enter or Space; pairing never starts merely by
opening an unpaired app. Enter the displayed six-digit passkey on the remote
computer. SolarOS stores the BLE bond, and the app records successful bonding
so later runs advertise for the remembered host automatically.

Open **Settings** and choose **Pair new host** to pair or replace a host. The
old host may need Bluetooth disabled while the new computer connects. The
`--pair` argument is the explicit command-line equivalent.

## Controls

- Pointer: moving over an outer hex highlights it. A touch press or mouse click
  triggers the highlighted hex; the last pointer coordinate remains highlighted.
- Joystick: normalized X/Y coordinates highlight a hex, while the physical
  neutral position highlights the center. Enter or Space triggers;
  automatic trigger also supports joysticks without a button.
- Keyboard: Left or Up advances around the six outer hexagons; Right or Down
  moves in the opposite direction. Space or Enter triggers the highlighted hex.
- Settings: press Tab from the ring. Use Up/Down to move, Left/Right or `-`/`+`
  to change dwell time, Enter or Space to activate, and Escape to return.
- Escape exits.

The graphics scale from the actual display height. All seven hexagons have the
same size and form a compact honeycomb. Hover uses a single solid, thick outline
that remains crisp on 1bpp displays; triggering briefly inverts the complete
hexagon.
On landscape displays the ring uses nearly all available height, while Settings,
the one-line BLE state, and the last-typed preview occupy the side margins.

## Arguments

```text
--auto-trigger             enable dwell triggering
--no-auto-trigger          disable dwell triggering
--dwell-ms 300..2000       set the dwell interval
--pair                     explicitly start host pairing at launch
```

Settings persist on the preferred SolarOS storage volume. BLE HID requires
SolarOS 4.11.2 or later with Python, graphics, generic input, and BLE support.
If BLE support is absent or disabled for the current boot, the app shows a
message and exits before opening graphics. Run `ble enable` and reboot to
enable BLE before launching the app.

## Origins and license

The interaction follows the Hex-O-Spell model described in the 2006 TU Berlin
paper *The Berlin Brain-Computer Interface presents the novel mental
typewriter Hex-o-Spell* and Marton Juhasz's 2016 joystick adaptation. This port
is distributed under the MIT License in `LICENSE`.
