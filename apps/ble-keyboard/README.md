# BLE Keyboard

Forward a local keyboard to a computer, phone, or tablet over BLE.
The upper half of the screen contains input and paired-host lists. The bottom
half shows security events and accepted key transmissions. Transmission logs
confirm that SolarOS accepted a report, rather than receipt by the remote app.

Run `playground run ble-keyboard`, or open BLE Keyboard in Playground.
The first ready input keyboard is selected automatically, including a sole
`keyboard0`. **Tab switches between the input and host sections.** Up/Down moves
within the focused list; Enter selects the input or requests a connection to the
selected saved host. **I rescans input devices.** In the host section, **d deletes
the selected host pairing**. Hosts display their discovered name or your label,
with the Bluetooth identity address as fallback. **N edits the selected host's
name**; Enter saves, Esc cancels, and Backspace erases. Save an empty label to
restore the discovered name or address. The local input keyboard's bond is
preserved.

Host names are cached in `/.ble-keyboard/hosts.json` on mounted storage (SD when
available, otherwise flash). The app loads this file once and saves only when
names change. Manual labels survive later name discovery; deleting a pairing
also removes its cached name. Failed saves appear in the log and keep names in
memory for the current run. An interrupted save can recover from a temporary
file or backup. The app uses the same storage volume until it exits.

Firmware with remote host-name support reads the host's Bluetooth GAP Device
Name asynchronously after security completes. Some hosts do not provide an
accessible name. Lookup does not delay forwarding; cached names appear before
connecting. Older host-management firmware still supports manual labels.

**P starts pairing with a new host.** Choose **SolarOS Keyboard** in the host's
Bluetooth settings and enter any displayed six-digit passkey there. Pairing
mode drops the current host connection and advertises a fresh peripheral
identity, so an old paired host cannot take that pairing offer. Other saved host
pairings remain in the host list. Launching with `--pair` starts this mode
immediately. Opening the app normally starts idle; choose a saved host with
Enter to offer it a connection. Wake that host or select its Bluetooth entry
if it does not connect automatically. BLE hosts initiate the connection.

The local keyboard stays available for selection and pairing controls until a
host is connected, bonded, encrypted, and accepting keyboard reports. Then the
selected input is captured and forwarded automatically. Esc cancels a host
connection offer, pairing, or security negotiation; Esc while idle quits.
Security failures return to selection without an automatic reconnect loop.

While forwarding, **Ctrl+Alt+Del quits** and releases remote keys. Every other
key from the selected physical source, including Tab, Esc, navigation keys, and
modifier chords, is forwarded. To change selections while forwarding, quit and
reopen the app. Disconnect releases capture and restores the setup controls.
The selected host may reconnect after an ordinary disconnection; capture resumes
when ready. Input loss and app exit also clear held keys. Keys entered while
disconnected are discarded. The remote host generates repeat while a key is held.

Requires a SolarOS build with BLE HID host management and keyboard capture.
Builds without those APIs show a requirement message. Pairings and host identity
metadata are stored by SolarOS in NVS. A selectable local keyboard must provide
canonical HID usages and key transitions; serial or telnet character input is
not a physical keyboard source. The host determines the keyboard layout. The
BLE report supports six ordinary held keys plus modifiers; consumer/media
reports are not supported.
