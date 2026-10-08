"""Select a local keyboard and a BLE host, then forward physical key events."""

import sys
import json
import solaros

DEVICE_NAME = "SolarOS Keyboard"
MAX_LOG = 48
DATA_DIR = "/.ble-keyboard"
MAX_NAME = 63
MAX_HOST_NAMES = 32
MAX_NAMES_FILE = 32768


def host_key(host):
    return "%d:%s" % (host["addr_type"], host["address"].lower())


def clean_name(value):
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value if ord(c) >= 32 and not 127 <= ord(c) <= 159).strip()[:MAX_NAME]


class HostNames:
    """Names live on the selected mounted filesystem; bonds stay in NVS."""
    def __init__(self, note):
        self.note = note
        self.records = {}
        self.storage = getattr(solaros, "storage", None)
        self.directory = None
        if self.storage:
            try:
                self.directory = self.storage.resolve(DATA_DIR)
            except OSError as exc:
                self.note("Host name storage unavailable: " + str(exc))
        if not self.directory:
            self.note("Host names will only be kept for this run")
            return
        self.path = self.directory + "/hosts.json"
        self.load()

    def load(self):
        damaged = False
        for path in (self.path, self.path + ".tmp", self.path + ".bak"):
            try:
                if not self.storage.exists(path):
                    continue
                raw = self.storage.read_file(path, MAX_NAMES_FILE + 1)
                if len(raw) > MAX_NAMES_FILE:
                    raise ValueError("name file too large")
                data = json.loads(raw.decode())
                if not isinstance(data, dict) or data.get("version") != 1:
                    raise ValueError("invalid name file")
                records = data.get("hosts")
                if not isinstance(records, dict) or len(records) > MAX_HOST_NAMES:
                    raise ValueError("invalid host names")
                for key, record in records.items():
                    # Include address type so public/random identities cannot collide.
                    if not isinstance(key, str) or len(key) != 19 or key[:2] not in ("0:", "1:"):
                        raise ValueError("invalid host identity")
                    parts = key[2:].split(":")
                    if len(parts) != 6 or any(len(p) != 2 or
                            any(c not in "0123456789abcdef" for c in p) for p in parts):
                        raise ValueError("invalid host address")
                    if not isinstance(record, dict) or set(record) != {"name", "label"}:
                        raise ValueError("invalid host record")
                    if any(not isinstance(v, str) or clean_name(v) != v for v in record.values()):
                        raise ValueError("invalid host name")
                self.records = records
                if path != self.path:
                    self.note("Recovered host names from interrupted save")
                return
            except (OSError, ValueError):
                damaged = True
        if damaged:
            self.note("Host names unavailable; using Bluetooth addresses")

    def save(self):
        if not self.directory:
            raise OSError("no mounted storage")
        if not self.storage.exists(self.directory):
            self.storage.mkdir(self.directory)
        temporary, backup = self.path + ".tmp", self.path + ".bak"
        data = json.dumps({"version": 1, "hosts": self.records})
        if len(data.encode()) > MAX_NAMES_FILE:
            raise ValueError("name file too large")
        self.storage.write_file(temporary, data)
        # Preserve the last committed file if promotion is interrupted.
        had_primary = self.storage.exists(self.path)
        if had_primary:
            if self.storage.exists(backup):
                self.storage.remove(backup)
            self.storage.rename(self.path, backup)
        try:
            self.storage.rename(temporary, self.path)
        except OSError:
            if had_primary:
                self.storage.rename(backup, self.path)
            raise
        if self.storage.exists(backup):
            self.storage.remove(backup)

    def update(self, hosts):
        keys = set(host_key(host) for host in hosts)
        records = {key: value.copy() for key, value in self.records.items() if key in keys}
        for host in hosts:
            name = clean_name(host.get("name"))
            if name:
                key = host_key(host)
                record = records.get(key, {"name": "", "label": ""})
                record["name"] = name
                records[key] = record
        self.replace(records)

    def replace(self, records):
        if records == self.records:
            return
        if len(records) > MAX_HOST_NAMES:
            self.note("Too many host names to save")
            return
        self.records = records
        try:
            self.save()
        except (OSError, ValueError) as exc:
            self.note("Host names not saved: " + str(exc))

    def label(self, host, label):
        key = host_key(host)
        records = {k: v.copy() for k, v in self.records.items()}
        record = records.get(key, {"name": "", "label": ""})
        record["label"] = clean_name(label)
        if record["name"] or record["label"]:
            records[key] = record
        else:
            records.pop(key, None)
        self.replace(records)

    def display(self, host):
        record = self.records.get(host_key(host), {})
        return record.get("label") or record.get("name") or clean_name(host.get("name")) or host["address"]


class KeyboardApp:
    def __init__(self):
        self.input = solaros.input
        self.tui = solaros.tui
        self.hid = solaros.ble.hid
        self.devices = []
        self.hosts = []
        self.selected = 0
        self.host_index = 0
        self.focus = "input"
        self.source = None
        self.captured = self.forwarding = False
        self.forward_requested = True
        self.running = True
        self.started = False
        self.pending = None
        self.retry_at = self.attempts = 0
        self.passkey = None
        self.status = {}
        self.ready = self.release_pending = False
        self.held = {}
        self.sent = set()
        self.log = []
        self.dirty = True
        self.last_status = self.last_draw = self.last_input_check = -1000
        self.dimensions = None
        self.edit_host = None
        self.edit_text = ""
        self.host_names = HostNames(self.note)
        self.modifier_keys = (
            self.hid.KEY_LEFT_CTRL, self.hid.KEY_LEFT_SHIFT,
            self.hid.KEY_LEFT_ALT, self.hid.KEY_LEFT_GUI,
            self.hid.KEY_RIGHT_CTRL, self.hid.KEY_RIGHT_SHIFT,
            self.hid.KEY_RIGHT_ALT, self.hid.KEY_RIGHT_GUI,
        )
        self.names = {}
        for name in dir(self.hid):
            if name.startswith("KEY_"):
                self.names[getattr(self.hid, name)] = name[4:]
        self.refresh_devices()

    def note(self, text):
        self.log.append(text)
        if len(self.log) > MAX_LOG:
            del self.log[0]
        self.dirty = True

    def refresh_devices(self):
        old_name = self.devices[self.selected]["name"] if self.devices else None
        old_source = self.source
        self.devices = [d for d in self.input.sources()
                        if d.get("ready") and
                        d.get("source_class") == self.input.SOURCE_KEYBOARD and
                        d.get("capabilities", 0) & self.input.CAP_KEY_EVENTS]
        self.selected = 0
        for index, device in enumerate(self.devices):
            if device["name"] == old_name:
                self.selected = index
        # Preserve the explicit selection across rescans. Autoselect the first
        # source on startup or after the selected keyboard disappears.
        matches = [d for d in self.devices if old_source and
                   d["name"] == old_source["name"] and
                   d["source"] == old_source["source"]]
        self.source = matches[0] if matches else (self.devices[0] if self.devices else None)
        self.dirty = True

    def refresh_hosts(self):
        old = self.hosts[self.host_index] if self.hosts else None
        self.hosts = self.hid.hosts()
        self.host_names.update(self.hosts)
        self.host_index = 0
        for index, host in enumerate(self.hosts):
            if old and (host["address"], host["addr_type"]) == (old["address"], old["addr_type"]):
                self.host_index = index
        self.dirty = True

    def release_keys(self):
        self.release_pending = False
        if self.started:
            try:
                self.hid.keyboard.release_all()
            except OSError:
                self.release_pending = True
        self.held.clear()
        self.sent.clear()

    def stop_forwarding(self):
        self.release_keys()
        if self.captured:
            self.input.release_keyboard()
            self.captured = False
        self.forwarding = False
        self.dirty = True

    def activate_forwarding(self):
        if not self.forward_requested or not self.ready or self.captured or self.source is None or self.edit_host:
            return
        try:
            self.input.capture_keyboard(self.source["name"])
        except OSError as exc:
            self.forward_requested = False
            self.note("Input: " + str(exc))
            return
        self.captured = self.forwarding = True
        self.note("Forwarding: " + self.source["name"])

    def begin_forwarding(self):
        if not self.devices:
            self.note("No ready keyboard; I rescans")
            return
        self.source = self.devices[self.selected]
        self.forward_requested = True
        self.note("Input: " + self.source["name"])
        self.activate_forwarding()

    def request(self, action, host=None):
        self.stop_forwarding()
        self.ready = False
        self.passkey = None
        self.pending = (action, host)
        self.attempts = 0
        self.retry_at = solaros.time.uptime_ms()
        self.forward_requested = True
        self.dirty = True

    def begin_pairing(self):
        self.request("pair")

    def cancel_pairing(self):
        self.pending = None
        self.stop_forwarding()
        self.hid.disconnect()
        self.ready = False
        self.status = {}
        self.passkey = None
        self.note("Host disconnected; select a host or press P")

    def poll(self, now):
        if self.pending and now >= self.retry_at:
            action, host = self.pending
            try:
                if action == "pair":
                    self.hid.pair()
                    self.note("Pair: " + DEVICE_NAME)
                else:
                    self.hid.connect(host["address"], host["addr_type"])
                    self.note("Waiting for host: " + self.host_names.display(host))
                self.pending = None
            except OSError as exc:
                self.attempts += 1
                self.retry_at = now + 150
                if self.attempts >= 20:
                    self.pending = None
                    self.note("BLE request failed: " + str(exc))
        if not self.started:
            return
        for _ in range(16):
            event = self.hid.poll()
            if event is None:
                break
            kind = event.get("type")
            if kind == "connected":
                self.note("Host connected; negotiating security")
            elif kind == "passkey":
                self.passkey = event["passkey"]
                self.note("Enter %06d on the host" % self.passkey)
            elif kind == "secured":
                code = event.get("status", 0)
                if code:
                    self.note("BLE security failed: 0x%03x" % code)
                elif event.get("encrypted") and event.get("bonded"):
                    self.passkey = None
                    self.refresh_hosts()
                    self.note("Host secured")
                else:
                    self.note("Host security incomplete")
            elif kind == "disconnected":
                self.stop_forwarding()
                self.ready = False
                self.passkey = None
                self.refresh_hosts()
                self.note("Host disconnected; keys discarded")
                if event.get("status"):
                    self.note("BLE disconnect: 0x%03x" % event["status"])
            self.dirty = True
        if now - self.last_status < 250:
            return
        self.last_status = now
        status = self.hid.status()
        ready = bool(status.get("connected") and status.get("encrypted") and
                     status.get("bonded") and status.get("keyboard_subscribed"))
        if self.ready and not ready:
            self.stop_forwarding()
        if ready and not self.ready:
            self.note("Host ready")
        if self.status != status:
            self.dirty = True
        if status.get("host_name") and status.get("host_name") != self.status.get("host_name"):
            self.refresh_hosts()
            self.note("Host name: " + clean_name(status["host_name"]))
        self.status = status
        self.ready = ready
        if not self.pending:
            self.activate_forwarding()

    def key_event(self, event):
        if event.get("type") == "reset":
            self.release_keys()
            self.note("Input reset; releasing remote keys")
            return
        if event.get("source") != self.source["source"]:
            return
        usage = event.get("usage", 0)
        modifiers = event.get("modifiers", 0)
        action = event["action"]
        if (action != self.input.KEY_RELEASE and usage == self.hid.KEY_DELETE and
                modifiers & self.input.MOD_CTRL and modifiers & self.input.MOD_ALT):
            self.release_keys()
            self.running = False
            return
        # Holding a BLE key lets the host generate its own repeat.
        if action == self.input.KEY_REPEAT:
            return
        if not self.ready or self.release_pending:
            self.held.clear()
            return
        physical = event.get("physical_key") or usage
        if action == self.input.KEY_RELEASE:
            self.held.pop(physical, None)
        elif usage and not 0xe0 <= usage <= 0xe7:
            self.held[physical] = usage
        elif event.get("key") and not usage:
            self.note("No HID usage for local key %d" % event["key"])
            return
        desired = set(self.held.values())
        for bit, key in enumerate(self.modifier_keys):
            if modifiers & (1 << bit):
                desired.add(key)
        released = sorted(self.sent - desired)
        pressed = sorted(desired - self.sent)
        try:
            if released:
                for offset in range(0, len(released), 8):
                    self.hid.keyboard.release(*released[offset:offset + 8])
                self.note("TX up   " + self.describe(released))
            if pressed:
                for offset in range(0, len(pressed), 8):
                    self.hid.keyboard.press(*pressed[offset:offset + 8])
                self.note("TX down " + self.describe(pressed))
            self.sent = desired
        except OSError as exc:
            self.release_keys()
            self.note("TX failed: " + str(exc))

    def describe(self, keys):
        return " ".join(self.names.get(key, "0x%02x" % key) for key in keys)

    def setup_key(self, key):
        if self.edit_host:
            if key in (10, 13):
                if any(host_key(h) == host_key(self.edit_host) for h in self.hosts):
                    self.host_names.label(self.edit_host, self.edit_text)
                    self.note("Host name: " + self.host_names.display(self.edit_host))
                else:
                    self.note("Host pairing disappeared; name not saved")
                self.edit_host = None
            elif key == self.tui.KEY_ESCAPE:
                self.edit_host = None
            elif key in (8, 127, getattr(self.tui, "KEY_BACKSPACE", -1)):
                self.edit_text = self.edit_text[:-1]
            elif 32 <= key <= 126 and len(self.edit_text) < MAX_NAME:
                self.edit_text += chr(key)
            self.dirty = True
            return
        if key == 9:
            self.focus = "hosts" if self.focus == "input" else "input"
        elif key in (self.tui.KEY_UP, self.tui.KEY_DOWN):
            step = -1 if key == self.tui.KEY_UP else 1
            if self.focus == "input" and self.devices:
                self.selected = (self.selected + step) % len(self.devices)
            elif self.focus == "hosts" and self.hosts:
                self.host_index = (self.host_index + step) % len(self.hosts)
        elif key in (10, 13):
            if self.focus == "input":
                self.begin_forwarding()
            elif self.hosts:
                self.request("connect", self.hosts[self.host_index].copy())
            else:
                self.note("No paired hosts; P pairs a new host")
        elif key in (ord("p"), ord("P")):
            self.begin_pairing()
        elif key in (ord("i"), ord("I")):
            self.refresh_devices()
            self.forward_requested = True
            self.note("Inputs rescanned")
        elif key in (ord("n"), ord("N")) and self.focus == "hosts" and self.hosts:
            self.edit_host = self.hosts[self.host_index].copy()
            record = self.host_names.records.get(host_key(self.edit_host), {})
            self.edit_text = record.get("label") or record.get("name") or clean_name(self.edit_host.get("name"))
        elif key in (ord("d"), ord("D")) and self.focus == "hosts" and self.hosts:
            host = self.hosts[self.host_index]
            try:
                self.hid.forget(host["address"], host["addr_type"])
                if self.pending and self.pending[1] and host_key(self.pending[1]) == host_key(host):
                    self.pending = None
                self.refresh_hosts()
                self.note("Deleted pairing: " + host["address"])
            except OSError as exc:
                self.note("Delete failed: " + str(exc))
        elif key == self.tui.KEY_ESCAPE:
            if self.pending or self.status.get("advertising") or self.status.get("connected"):
                try:
                    self.cancel_pairing()
                except OSError as exc:
                    self.note("Disconnect failed: " + str(exc))
            else:
                self.running = False
        self.dirty = True

    def draw(self, now):
        rows, cols = self.tui.size()
        self.dimensions = (rows, cols)
        self.tui.clear()
        middle = rows // 2
        def put(row, text, attr=0, fill=False):
            if 0 <= row < rows:
                text = text.replace("\r", " ").replace("\n", " ")[:cols]
                self.tui.addstr(row, 0, text + " " * (cols - len(text)) if fill else text, attr)
        state = ("Forwarding" if self.forwarding else
                 "Host ready; select input" if self.ready else
                 "Securing host" if self.status.get("connected") else
                 "Preparing" if self.pending else
                 "Pairing" if self.status.get("pairing") and self.status.get("advertising") else
                 "Preparing pairing" if self.status.get("pairing") else
                 "Waiting for selected host" if self.status.get("advertising") else "Idle")
        if self.passkey is not None:
            state = "Enter %06d on host" % self.passkey
        put(0, " BLE Keyboard | " + state, self.tui.INVERSE, True)
        if middle >= 8:
            put(1, "Tab section  Up/Down choose  Enter select", self.tui.NORMAL)
            room = middle - 6
            input_room = max(1, room // 2)
            host_room = max(1, room - input_room)
            def section(row, label, items, cursor, focus, count, name):
                put(row, ("> " if self.focus == focus else "  ") + label,
                    self.tui.BOLD)
                first = max(0, cursor - count + 1)
                if not items:
                    put(row + 1, "  None ready" if focus == "input" else "  None paired; P to pair")
                for index in range(first, min(len(items), first + count)):
                    item = items[index]
                    marker = "*" if (focus == "input" and self.source and
                                    item["source"] == self.source["source"]) or item.get("connected") else " "
                    text = marker + " " + name(item)
                    put(row + 1 + index - first, text,
                        self.tui.INVERSE if self.focus == focus and index == cursor else self.tui.NORMAL)
            section(2, "Inputs (I rescan)", self.devices, self.selected, "input", input_room,
                    lambda item: item["name"])
            put(3 + input_room, "-" * cols)
            section(4 + input_room, "Paired hosts (N name, d delete)", self.hosts, self.host_index, "hosts", host_room,
                    self.host_names.display)
            put(middle - 1, "P new pairing  Esc disconnect / quit")
        else:
            source = self.devices[self.selected]["name"] if self.devices else "none"
            host = self.host_names.display(self.hosts[self.host_index]) if self.hosts else "none"
            if middle > 1:
                put(1, "Input: " + source, self.tui.INVERSE if self.focus == "input" else self.tui.NORMAL)
            if middle > 2:
                put(2, "Host: " + host, self.tui.INVERSE if self.focus == "hosts" else self.tui.NORMAL)
            if middle > 3:
                put(3, "Tab switch  I scan  N name  d delete  P pair")
        if rows > 2:
            put(middle, "-" * cols)
            if rows > middle + 2:
                put(middle + 1, " Transmissions / log", self.tui.BOLD)
            room = max(0, rows - middle - 3)
            for index, line in enumerate(self.log[-room:] if room else []):
                put(middle + 2 + index, line)
        if self.edit_host:
            visible = self.edit_text[-(cols - 7):] if cols > 7 else ""
            put(max(1, middle - 1), "Name: " + visible + "_", self.tui.INVERSE, True)
        if rows > 1:
            help_text = ("Enter save  Esc cancel  Backspace erase; empty = auto" if self.edit_host else
                         "Ctrl+Alt+Del quit; other keys -> BLE" if self.forwarding else
                         "Tab switch  Enter select  I scan  N name  d delete  P pair")
            put(rows - 1, help_text, self.tui.INVERSE, True)
        self.tui.refresh()
        self.last_draw = now
        self.dirty = False

    def run(self, pair=False):
        self.hid.start(DEVICE_NAME, True)
        self.started = True
        self.refresh_hosts()
        if pair:
            self.begin_pairing()
        while self.running and not solaros.should_exit():
            now = solaros.time.uptime_ms()
            self.poll(now)
            if self.release_pending and self.ready:
                self.release_keys()
            if self.forwarding:
                for _ in range(32):
                    event = self.input.read_key()
                    if event is None:
                        break
                    self.key_event(event)
                    if not self.running:
                        break
                self.tui.getch(0)
                if now - self.last_input_check >= 1000:
                    self.last_input_check = now
                    if not any(d["source"] == self.source["source"] and d.get("ready") and
                               d["name"] == self.source["name"] for d in self.input.sources()):
                        self.stop_forwarding()
                        self.forward_requested = False
                        self.refresh_devices()
                        self.note("Local input unavailable; select again")
            else:
                key = self.tui.getch(0)
                if key is not None:
                    self.setup_key(key)
            if self.tui.size() != self.dimensions:
                self.dirty = True
            if self.dirty and now - self.last_draw >= 100:
                self.draw(now)
            solaros.time.sleep_ms(20)

    def shutdown(self):
        try:
            self.stop_forwarding()
        finally:
            if self.started:
                self.hid.stop()
                self.started = False


def main():
    if "--help" in sys.argv[1:]:
        print("BLE Keyboard: [--pair] (start in new-host pairing mode)")
        return
    if any(argument != "--pair" for argument in sys.argv[1:]):
        raise ValueError("Usage: ble-keyboard [--pair]")
    device_input = getattr(solaros, "input", None)
    hid = getattr(getattr(solaros, "ble", None), "hid", None)
    if hid is None or device_input is None or not all(hasattr(hid, name) for name in
            ("hosts", "connect", "forget", "disconnect")):
        print("BLE Keyboard requires firmware with BLE HID host management.")
        return
    if not all(hasattr(device_input, name) for name in
               ("capture_keyboard", "release_keyboard", "read_key")):
        print("BLE Keyboard requires firmware with keyboard capture.")
        return
    app = KeyboardApp()
    try:
        solaros.tick_interval(10)
        app.run("--pair" in sys.argv[1:])
    finally:
        app.shutdown()


main()
