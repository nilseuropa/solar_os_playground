"""Hex-O-Spell radial BLE keyboard for SolarOS."""

import sys

import solaros
from solaros import gfx
from solaros import input as device_input

from hex_o_spell_core import (
    DIRECTIONS,
    DwellTrigger,
    HexOSpellState,
    SETTINGS_AUTO,
    SETTINGS_DONE,
    SETTINGS_DWELL,
    SETTINGS_EXIT,
    SETTINGS_PAIR,
    cycle_direction,
    joystick_direction,
    move_settings_focus,
    point_in_hex,
)


CONFIG_PATH = "/.hex-o-spell.cfg"
DEVICE_NAME = "Hex-O-Spell"
DEFAULT_DWELL_MS = 1000
KEY_HOLD_MS = 30
TRIGGER_FLASH_MS = 80
TICK_INTERVAL_MS = 5
MIN_DWELL_MS = 300
MAX_DWELL_MS = 2000
DWELL_STEP_MS = 100
JOYSTICK_DEADZONE = 10000
HOVER_STROKE = 6
SETTINGS_ACTION_HEIGHT = 44
SETTINGS_ACTION_BOTTOM_MARGIN = 28

# USB HID usages that are accepted by the typed BLE API but do not yet have
# public KEY_* names in SolarOS 4.11.2.
HID_KEY_PERIOD = 0x37
HID_KEY_SLASH = 0x38


def clamp(value, low, high):
    return max(low, min(high, value))


def parse_arguments(arguments, stored_auto, stored_dwell):
    auto_trigger = stored_auto
    dwell_ms = stored_dwell
    request_pairing = False
    index = 1
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--auto-trigger":
            auto_trigger = True
        elif argument == "--no-auto-trigger":
            auto_trigger = False
        elif argument == "--pair":
            request_pairing = True
        elif argument == "--dwell-ms":
            index += 1
            if index >= len(arguments):
                raise ValueError("--dwell-ms requires a value")
            dwell_ms = int(arguments[index])
            if dwell_ms < MIN_DWELL_MS or dwell_ms > MAX_DWELL_MS:
                raise ValueError("--dwell-ms must be 300..2000")
        elif argument in ("-h", "--help"):
            print("Usage: hex_o_spell.py [--auto-trigger|--no-auto-trigger]")
            print("       [--dwell-ms 300..2000] [--pair]")
            return None
        else:
            raise ValueError("unknown argument: " + argument)
        index += 1
    return auto_trigger, dwell_ms, request_pairing


def load_config():
    config = {"paired": False, "auto": False, "dwell": DEFAULT_DWELL_MS}
    try:
        with open(CONFIG_PATH, "r") as source:
            for line in source:
                parts = line.strip().split("=", 1)
                if len(parts) != 2:
                    continue
                key, value = parts
                if key == "paired":
                    config["paired"] = value == "1"
                elif key == "auto":
                    config["auto"] = value == "1"
                elif key == "dwell":
                    config["dwell"] = clamp(
                        int(value), MIN_DWELL_MS, MAX_DWELL_MS
                    )
    except (OSError, ValueError):
        pass
    return config


def save_config(paired, auto_trigger, dwell_ms):
    try:
        with open(CONFIG_PATH, "w") as output:
            output.write("paired=%d\n" % (1 if paired else 0))
            output.write("auto=%d\n" % (1 if auto_trigger else 0))
            output.write("dwell=%d\n" % dwell_ms)
    except OSError:
        # A read-only or unavailable store must not make the keyboard unusable.
        pass


class HexOSpellApp:
    def __init__(self, auto_trigger, dwell_ms, remembered, request_pairing):
        self.hid = getattr(getattr(solaros, "ble", None), "hid", None)
        if self.hid is None:
            raise RuntimeError("SolarOS BLE HID API is unavailable")

        self.w, self.h = gfx.size()
        self.cx = self.w // 2
        self.cy = self.h // 2
        board_half = min(self.w, self.h) // 2
        edge_margin = max(4, self.h // 60)
        cell_gap = max(3, self.h // 80)
        self.cell_half_height = max(
            26, (board_half - edge_margin - cell_gap) // 3
        )
        self.cell_radius = self.cell_half_height * 1000 // 866
        self.ring_radius = self.cell_half_height * 2 + cell_gap
        self.center_radius = self.cell_radius
        self.hex_fill_chunks = self.make_hex_fill_chunks()
        self.hex_hover_chunks = self.make_hex_fill_chunks(HOVER_STROKE)
        self.centers = []
        for dx, dy in DIRECTIONS:
            self.centers.append(
                (
                    self.cx + dx * self.ring_radius // 1000,
                    self.cy + dy * self.ring_radius // 1000,
                )
            )

        self.state = HexOSpellState()
        self.auto_trigger = auto_trigger
        self.dwell = DwellTrigger(dwell_ms)
        self.remembered = remembered
        self.hid_started = False
        self.start_pending = False
        self.pending_since = 0
        self.pairing = False
        self.passkey = None
        self.popup = None
        self.error = ""
        self.settings = False
        self.settings_focus = SETTINGS_AUTO
        self.running = True
        self.dirty = True
        self.selection_update = False
        self.selection_previous = None
        self.preview_dirty = False
        self.trigger_flash_direction = None
        self.trigger_flash_until = 0
        self.last_draw_ms = 0
        self.last_status_ms = 0
        self.hid_status = {}
        self.preview = ""
        self.notice = ""
        self.notice_until = 0
        self.selection_source = None
        self.pointer_x = self.cx
        self.pointer_y = self.cy
        self.joystick_x = 0
        self.joystick_y = 0

        if request_pairing:
            self.begin_pairing()
        elif remembered:
            if not self.start_hid(False):
                self.popup = "error"
        else:
            self.popup = "unpaired"

    def persist(self):
        save_config(self.remembered, self.auto_trigger, self.dwell.duration_ms)

    def start_hid(self, pairing):
        try:
            self.hid.start(DEVICE_NAME)
            self.hid_started = True
            if pairing:
                self.hid.pair()
            self.start_pending = False
            self.pairing = pairing
            if pairing:
                self.popup = "pairing"
            self.error = ""
            self.dirty = True
            return True
        except OSError as exc:
            self.error = str(exc)
            if self.hid_started:
                self.stop_hid()
            return False

    def stop_hid(self):
        if not self.hid_started:
            return
        try:
            self.hid.keyboard.release_all()
        except OSError:
            pass
        try:
            self.hid.stop()
        except OSError:
            pass
        self.hid_started = False
        self.hid_status = {}

    def begin_pairing(self):
        self.passkey = None
        self.popup = "pairing"
        self.pairing = True
        self.pending_attempts = 0
        if self.hid_started:
            self.stop_hid()
            self.start_pending = True
            self.pending_since = solaros.time.uptime_ms()
        elif not self.start_hid(True):
            self.popup = "error"
        self.dirty = True

    def retry_pending_start(self, now_ms):
        if not self.start_pending or now_ms - self.pending_since < 150:
            return
        self.pending_since = now_ms
        if self.start_hid(True):
            return
        # Stopping is asynchronous. Keep retrying for three seconds before
        # presenting an actionable error.
        self.pending_attempts += 1
        if self.pending_attempts >= 20:
            self.start_pending = False
            self.popup = "error"
            self.dirty = True

    def poll_hid(self):
        if not self.hid_started:
            return
        while True:
            try:
                event = self.hid.poll()
            except OSError as exc:
                self.error = str(exc)
                self.dirty = True
                return
            if event is None:
                return
            kind = event.get("type", "")
            if kind == "passkey":
                self.passkey = event.get("passkey", 0)
                self.popup = "pairing"
                self.pairing = True
            elif kind == "secured" and event.get("bonded", False):
                self.remembered = True
                self.pairing = False
                self.popup = None
                self.persist()
            elif kind == "disconnected":
                self.passkey = None
                if self.pairing:
                    self.popup = "pairing"
                self.notice = "Host disconnected"
                self.notice_until = solaros.time.uptime_ms() + 1800
            self.dirty = True

    def refresh_hid_status(self, now_ms):
        if not self.hid_started or now_ms - self.last_status_ms < 250:
            return
        self.last_status_ms = now_ms
        try:
            status = self.hid.status()
        except OSError:
            status = {}
        if status != self.hid_status:
            self.hid_status = status
            self.dirty = True
        if status.get("bonded", False) and not self.remembered:
            self.remembered = True
            self.pairing = False
            self.popup = None
            self.persist()

    def keyboard_ready(self):
        return self.hid_status.get("keyboard_subscribed", False)

    def keyboard_notice(self):
        if not self.hid_status.get("connected", False):
            return "Connect host first"
        if not self.hid_status.get("encrypted", False):
            return "Securing host..."
        return "Host keyboard inactive"

    def send_character(self, character):
        if not self.keyboard_ready():
            self.notice = self.keyboard_notice()
            self.notice_until = solaros.time.uptime_ms() + 1800
            self.dirty = True
            return False

        if character == "\b":
            keys = (self.hid.KEY_BACKSPACE,)
        elif character == " ":
            keys = (self.hid.KEY_SPACE,)
        elif character == ".":
            keys = (getattr(self.hid, "KEY_PERIOD", HID_KEY_PERIOD),)
        elif character == "?":
            keys = (
                self.hid.KEY_LEFT_SHIFT,
                getattr(self.hid, "KEY_SLASH", HID_KEY_SLASH),
            )
        else:
            keys = (self.hid.KEY_LEFT_SHIFT, getattr(self.hid, "KEY_" + character))

        try:
            self.hid.keyboard.press(*keys)
            solaros.time.sleep_ms(KEY_HOLD_MS)
            self.hid.keyboard.release_all()
        except OSError as exc:
            try:
                self.hid.keyboard.release_all()
            except OSError:
                pass
            self.notice = "HID send failed"
            self.notice_until = solaros.time.uptime_ms() + 1800
            self.error = str(exc)
            self.dirty = True
            return False

        if character == "\b":
            self.preview = self.preview[:-1]
        else:
            self.preview = (self.preview + character)[-16:]
        self.preview_dirty = True
        return True

    def trigger(self, now_ms):
        direction = self.state.selection
        if direction is None:
            return
        previous_level = self.state.level
        action = self.state.trigger()
        self.dwell.lock()
        self.trigger_flash_direction = direction
        self.trigger_flash_until = now_ms + TRIGGER_FLASH_MS
        if action is not None:
            self.send_character(action)
        if self.state.level != previous_level:
            self.dirty = True
        else:
            self.mark_selection_update(direction)

    def mark_selection_update(self, previous):
        if self.dirty:
            return
        if not self.selection_update:
            self.selection_previous = previous
        self.selection_update = True

    def select_direction(self, direction, source, now_ms):
        if self.selection_source != source or self.state.selection != direction:
            previous = self.state.selection
            self.state.select(direction)
            self.selection_source = source
            if self.auto_trigger:
                self.dwell.arm(source, direction, now_ms)
            else:
                self.dwell.cancel()
            if previous is None or self.trigger_flash_direction is not None:
                self.dirty = True
            else:
                self.mark_selection_update(previous)

    def center_selection(self, source):
        if self.state.center():
            # Removing the filled center cell through the compact bitmap mask
            # can leave stale wedges on 1bpp displays. Rebuild one clean frame.
            self.dirty = True
        self.selection_source = source
        self.dwell.cancel()

    def touched_direction(self, x, y):
        for direction, center in enumerate(self.centers):
            if point_in_hex(
                x - center[0],
                y - center[1],
                self.cell_radius,
                self.cell_half_height,
            ):
                return direction
        return None

    def handle_pointer(self, event, now_ms):
        if event.get("mode") == device_input.MODE_ABSOLUTE:
            self.pointer_x = clamp(event.get("x", 0), 0, self.w - 1)
            self.pointer_y = clamp(event.get("y", 0), 0, self.h - 1)
        else:
            self.pointer_x = clamp(
                self.pointer_x + event.get("delta_x", 0), 0, self.w - 1
            )
            self.pointer_y = clamp(
                self.pointer_y + event.get("delta_y", 0), 0, self.h - 1
            )
        action = event.get("action")
        x = self.pointer_x
        y = self.pointer_y
        if action == device_input.ACTION_PRESS:
            if self.popup is not None:
                self.handle_popup_touch(x, y)
                return
            if self.settings:
                self.handle_settings_touch(x, y)
                return
            if x <= 88 and y <= 42:
                self.settings = True
                self.settings_focus = SETTINGS_AUTO
                self.dirty = True
                return
        if self.popup is not None or self.settings:
            return
        direction = self.touched_direction(x, y)
        if direction is None:
            self.center_selection("pointer")
        else:
            self.select_direction(direction, "pointer", now_ms)
            if action == device_input.ACTION_PRESS:
                self.trigger(now_ms)

    def handle_axis(self, event, now_ms):
        if event.get("source_class") != device_input.SOURCE_JOYSTICK:
            return
        axis = event.get("axis")
        if axis == device_input.AXIS_X:
            self.joystick_x = event.get("value", 0)
        elif axis == device_input.AXIS_Y:
            self.joystick_y = event.get("value", 0)
        else:
            return
        direction = joystick_direction(
            self.joystick_x, self.joystick_y, JOYSTICK_DEADZONE
        )
        if direction is None:
            self.center_selection("joystick")
        else:
            self.select_direction(
                direction,
                "joystick",
                now_ms,
            )

    def handle_key(self, key, now_ms):
        if key is None:
            return
        if self.popup is not None:
            if key == gfx.KEY_ESCAPE:
                self.running = False
                return
            if key in (10, 13, 32):
                if self.popup in ("unpaired", "error"):
                    self.begin_pairing()
            return
        if self.settings:
            self.handle_settings_key(key)
            return
        if key == gfx.KEY_ESCAPE:
            self.running = False
            return
        if key == 9:  # Tab
            self.settings = True
            self.settings_focus = SETTINGS_AUTO
            self.dirty = True
            return

        if key in (gfx.KEY_LEFT, gfx.KEY_RIGHT, gfx.KEY_UP, gfx.KEY_DOWN):
            step = 1 if key in (gfx.KEY_LEFT, gfx.KEY_UP) else -1
            direction = cycle_direction(self.state.selection, step)
            self.select_direction(direction, "keyboard", now_ms)
        elif key in (10, 13, 32):
            self.trigger(now_ms)

    def adjust_dwell(self, delta):
        self.dwell.duration_ms = clamp(
            self.dwell.duration_ms + delta,
            MIN_DWELL_MS,
            MAX_DWELL_MS,
        )
        self.persist()

    def activate_setting(self):
        if self.settings_focus == SETTINGS_AUTO:
            self.auto_trigger = not self.auto_trigger
            self.dwell.cancel()
            self.persist()
        elif self.settings_focus == SETTINGS_PAIR:
            self.settings = False
            self.begin_pairing()
        elif self.settings_focus == SETTINGS_DONE:
            self.settings = False
            self.persist()
        elif self.settings_focus == SETTINGS_EXIT:
            self.running = False
        self.dirty = True

    def handle_settings_key(self, key):
        if key == gfx.KEY_ESCAPE:
            self.settings = False
            self.persist()
        elif key in (gfx.KEY_UP, gfx.KEY_DOWN, 9):
            direction = "up" if key == gfx.KEY_UP else "down"
            self.settings_focus = move_settings_focus(
                self.settings_focus, direction
            )
        elif key in (gfx.KEY_LEFT, gfx.KEY_RIGHT):
            if self.settings_focus == SETTINGS_DWELL:
                delta = -DWELL_STEP_MS if key == gfx.KEY_LEFT else DWELL_STEP_MS
                self.adjust_dwell(delta)
            else:
                direction = "left" if key == gfx.KEY_LEFT else "right"
                self.settings_focus = move_settings_focus(
                    self.settings_focus, direction
                )
        elif key in (43, 61) and self.settings_focus == SETTINGS_DWELL:
            self.adjust_dwell(DWELL_STEP_MS)
        elif key == 45 and self.settings_focus == SETTINGS_DWELL:
            self.adjust_dwell(-DWELL_STEP_MS)
        elif key in (10, 13, 32):
            self.activate_setting()
        self.dirty = True

    def handle_popup_touch(self, x, y):
        button_y = self.h * 2 // 3
        if y < button_y or y > button_y + 48:
            return
        if self.popup in ("unpaired", "error"):
            if x < self.cx:
                self.begin_pairing()
            else:
                self.running = False
        elif self.popup == "pairing":
            self.stop_hid()
            self.start_pending = False
            self.pairing = False
            self.popup = None if self.remembered else "unpaired"
            self.dirty = True

    def handle_settings_touch(self, x, y):
        bottom_y = self.h - SETTINGS_ACTION_BOTTOM_MARGIN - SETTINGS_ACTION_HEIGHT
        if 45 <= y <= 95:
            self.settings_focus = SETTINGS_AUTO
            self.activate_setting()
        elif 105 <= y <= 160:
            self.settings_focus = SETTINGS_DWELL
            if x < self.cx:
                self.adjust_dwell(-DWELL_STEP_MS)
            else:
                self.adjust_dwell(DWELL_STEP_MS)
        elif 170 <= y <= 220:
            self.settings_focus = SETTINGS_PAIR
            self.activate_setting()
        elif bottom_y <= y < bottom_y + SETTINGS_ACTION_HEIGHT:
            if x < self.cx:
                self.settings_focus = SETTINGS_DONE
            else:
                self.settings_focus = SETTINGS_EXIT
            self.activate_setting()
        self.dirty = True

    def draw_centered(self, x, baseline_y, text, font, char_width, color):
        gfx.font(font)
        gfx.color(color)
        gfx.text(x - len(text) * char_width // 2, baseline_y, text)

    def draw_right_aligned(
        self, right, baseline_y, text, font, char_width, color
    ):
        gfx.font(font)
        gfx.color(color)
        gfx.text(max(0, right - len(text) * char_width), baseline_y, text)

    def draw_hex(self, cx, cy, radius, color):
        half = radius // 2
        high = radius * 866 // 1000
        points = (
            (cx - radius, cy),
            (cx - half, cy - high),
            (cx + half, cy - high),
            (cx + radius, cy),
            (cx + half, cy + high),
            (cx - half, cy + high),
        )
        gfx.color(color)
        for index in range(6):
            a = points[index]
            b = points[(index + 1) % 6]
            gfx.line(a[0], a[1], b[0], b[1])

    def make_hex_fill_chunks(self, outline_width=0):
        width = self.cell_radius * 2 + 1
        height = self.cell_half_height * 2 + 1
        inner_radius = self.cell_radius - outline_width
        inner_half_height = self.cell_half_height - outline_width
        row_bytes = (width + 7) // 8
        rows_per_chunk = max(1, 128 // row_bytes)
        chunks = []
        start_row = 0
        while start_row < height:
            rows = min(rows_per_chunk, height - start_row)
            data = bytearray(row_bytes * rows)
            for row in range(rows):
                relative_y = start_row + row - self.cell_half_height
                extent = self.cell_radius - (
                    abs(relative_y) * self.cell_radius
                    // (2 * self.cell_half_height)
                )
                first = self.cell_radius - extent
                last = self.cell_radius + extent
                offset = row * row_bytes
                for pixel in range(first, last + 1):
                    if outline_width and abs(relative_y) <= inner_half_height:
                        inner_extent = inner_radius - (
                            abs(relative_y) * inner_radius
                            // (2 * inner_half_height)
                        )
                        relative_x = pixel - self.cell_radius
                        if -inner_extent <= relative_x <= inner_extent:
                            continue
                    data[offset + pixel // 8] |= 1 << (pixel & 7)
            chunks.append((start_row, rows, bytes(data)))
            start_row += rows
        return chunks

    def fill_hex(self, cx, cy, color, chunks=None):
        width = self.cell_radius * 2 + 1
        top = cy - self.cell_half_height
        left = cx - self.cell_radius
        gfx.color(color)
        if chunks is None:
            chunks = self.hex_fill_chunks
        for start_row, rows, data in chunks:
            gfx.bitmap(left, top + start_row, width, rows, data)

    def fill_hover(self, cx, cy, color):
        self.fill_hex(cx, cy, color, self.hex_hover_chunks)

    def clear_hex(self, cx, cy):
        # Compact neighboring hexes have overlapping bounding rectangles.
        # Clear through the same mask used for selection so a partial redraw
        # cannot erase a neighboring outline.
        self.fill_hex(cx, cy, gfx.WHITE)

    def draw_outer_cell(self, index, hovered, triggered, labels):
        x, y = self.centers[index]
        if triggered:
            self.fill_hex(x, y, gfx.BLACK)
        elif hovered:
            self.fill_hover(x, y, gfx.BLACK)
        self.draw_hex(
            x,
            y,
            self.cell_radius,
            gfx.WHITE if triggered else gfx.BLACK,
        )
        ink = gfx.WHITE if triggered else gfx.BLACK
        cell_labels = labels[index]
        if len(cell_labels) == 1:
            label = cell_labels[0]
            self.draw_centered(
                x, y + 7, label, gfx.FONT_BOLD_20, 12, ink
            )
            return
        for token_index, label in enumerate(cell_labels):
            dx, dy = DIRECTIONS[token_index]
            tx = x + dx * (self.cell_radius * 9 // 20) // 1000
            ty = y + dy * (self.cell_radius * 9 // 20) // 1000
            self.draw_centered(
                tx, ty + 6, label, gfx.FONT_BOLD_16, 9, ink
            )

    def center_text(self):
        if self.state.level == "groups":
            return "GROUPS"
        group = self.state.active_group
        return (
            "A-E" if group == 0 else
            "F-J" if group == 1 else
            "K-O" if group == 2 else
            "P-T" if group == 3 else
            "U-Y" if group == 4 else
            "TOOLS"
        )

    def draw_center_cell(self, selected, clear=False):
        if clear:
            self.clear_hex(self.cx, self.cy)
        if selected:
            self.fill_hex(self.cx, self.cy, gfx.BLACK)
        self.draw_hex(
            self.cx,
            self.cy,
            self.center_radius,
            gfx.WHITE if selected else gfx.BLACK,
        )
        self.draw_centered(
            self.cx,
            self.cy + 6,
            self.center_text(),
            gfx.FONT_BOLD_16,
            9,
            gfx.WHITE if selected else gfx.BLACK,
        )

    def draw_preview(self, clear=False):
        top = self.h - 43
        if clear:
            gfx.color(gfx.WHITE)
            gfx.fill_rect(
                3,
                top,
                max(1, self.centers[5][0] - self.cell_radius - 6),
                40,
            )
        gfx.color(gfx.BLACK)
        gfx.font(gfx.FONT_MONO_12)
        gfx.text(5, self.h - 27, "LAST")
        shown = self.preview.replace(" ", "_")
        gfx.text(5, self.h - 9, shown[-10:] if shown else "-")

    def draw_partial(self, now_ms):
        if self.selection_update:
            labels = self.state.labels()
            current = self.state.selection
            previous = self.selection_previous
            if previous is not None:
                x, y = self.centers[previous]
                self.fill_hover(x, y, gfx.WHITE)
                self.draw_outer_cell(
                    previous,
                    previous == current,
                    previous == self.trigger_flash_direction,
                    labels,
                )
            if current is not None and current != previous:
                self.draw_outer_cell(
                    current,
                    True,
                    current == self.trigger_flash_direction,
                    labels,
                )
        if self.preview_dirty:
            self.draw_preview(clear=True)
        gfx.present()
        self.selection_update = False
        self.selection_previous = None
        self.preview_dirty = False
        self.last_draw_ms = now_ms

    def draw_ring(self, now_ms):
        gfx.clear(gfx.WHITE)
        selected = self.state.selection
        labels = self.state.labels()
        for index in range(len(self.centers)):
            self.draw_outer_cell(
                index,
                selected == index,
                self.trigger_flash_direction == index,
                labels,
            )
        self.draw_center_cell(selected is None)

        # The ring uses essentially the full screen height. Controls and status
        # occupy only the landscape side margins.
        gfx.color(gfx.BLACK)
        settings_width = max(
            70, min(84, self.cx - self.ring_radius - self.cell_radius - 6)
        )
        gfx.rect(3, 3, settings_width, 34)
        gfx.font(gfx.FONT_BOLD_12)
        gfx.text(10, 25, "SETTINGS")
        self.draw_preview()

        if self.keyboard_ready():
            status = "READY"
        elif self.hid_status.get("connected", False):
            if not self.hid_status.get("encrypted", False):
                status = "AUTH"
            elif self.hid_status.get("bonded", False):
                status = "IDLE"
            else:
                status = "LINK"
        elif self.hid_started:
            status = "WAIT"
        else:
            status = "OFF"
        self.draw_right_aligned(
            self.w - 4,
            22,
            "BLE " + status,
            gfx.FONT_BOLD_12,
            7,
            gfx.BLACK,
        )
        if self.auto_trigger:
            self.draw_right_aligned(
                self.w - 4,
                self.h - 12,
                "AUTO %d" % self.dwell.duration_ms,
                gfx.FONT_BOLD_12,
                7,
                gfx.BLACK,
            )

        if self.notice and now_ms < self.notice_until:
            width = min(self.w - 40, len(self.notice) * 9 + 20)
            x = (self.w - width) // 2
            gfx.color(gfx.WHITE)
            gfx.fill_rect(x, self.h - 30, width, 26)
            gfx.color(gfx.BLACK)
            gfx.rect(x, self.h - 30, width, 26)
            self.draw_centered(
                self.cx, self.h - 12, self.notice, gfx.FONT_BOLD_12, 7, gfx.BLACK
            )

    def draw_button(self, x, y, width, height, label, inverted=False):
        if inverted:
            gfx.color(gfx.BLACK)
            gfx.fill_rect(x, y, width, height)
            color = gfx.WHITE
        else:
            gfx.color(gfx.WHITE)
            gfx.fill_rect(x, y, width, height)
            gfx.color(gfx.BLACK)
            gfx.rect(x, y, width, height)
            color = gfx.BLACK
        self.draw_centered(
            x + width // 2,
            y + height // 2 + 5,
            label,
            gfx.FONT_BOLD_14,
            8,
            color,
        )

    def draw_popup(self):
        gfx.clear(gfx.WHITE)
        margin = max(30, self.w // 8)
        top = max(24, self.h // 7)
        width = self.w - margin * 2
        height = self.h - top * 2
        gfx.color(gfx.BLACK)
        gfx.rect(margin, top, width, height)
        self.draw_centered(
            self.cx, top + 35, "BLE KEYBOARD", gfx.FONT_BOLD_18, 9, gfx.BLACK
        )
        if self.popup == "unpaired":
            line1 = "No remembered host"
            line2 = "Pairing starts only when you tap Pair"
        elif self.popup == "error":
            line1 = "Could not start BLE HID"
            line2 = self.error[:38]
        elif self.passkey is not None:
            line1 = "Enter this code on the computer"
            line2 = "%06d" % self.passkey
        elif self.start_pending:
            line1 = "Preparing pairing"
            line2 = "Please wait..."
        else:
            line1 = "Pair on the remote computer"
            line2 = DEVICE_NAME
        self.draw_centered(
            self.cx, top + 78, line1, gfx.FONT_BOLD_14, 8, gfx.BLACK
        )
        self.draw_centered(
            self.cx,
            top + 112,
            line2,
            gfx.FONT_BOLD_18 if self.passkey is not None else gfx.FONT_MONO_12,
            11 if self.passkey is not None else 7,
            gfx.BLACK,
        )
        button_y = self.h * 2 // 3
        if self.popup in ("unpaired", "error"):
            self.draw_button(55, button_y, self.cx - 65, 48, "PAIR", True)
            self.draw_button(self.cx + 10, button_y, self.cx - 65, 48, "EXIT")
        else:
            self.draw_button(self.cx - 80, button_y, 160, 48, "CANCEL")

    def draw_settings(self):
        gfx.clear(gfx.WHITE)
        self.draw_centered(
            self.cx, 30, "HEX-O-SPELL SETTINGS", gfx.FONT_BOLD_18, 9, gfx.BLACK
        )
        self.draw_button(
            45,
            45,
            self.w - 90,
            50,
            "AUTO TRIGGER: " + ("ON" if self.auto_trigger else "OFF"),
            self.settings_focus == SETTINGS_AUTO,
        )
        self.draw_button(45, 105, 85, 55, "-")
        self.draw_button(
            140,
            105,
            self.w - 280,
            55,
            "%d ms" % self.dwell.duration_ms,
            self.settings_focus == SETTINGS_DWELL,
        )
        self.draw_button(self.w - 130, 105, 85, 55, "+")
        self.draw_button(
            45,
            170,
            self.w - 90,
            50,
            "PAIR NEW HOST",
            self.settings_focus == SETTINGS_PAIR,
        )
        bottom_y = self.h - SETTINGS_ACTION_BOTTOM_MARGIN - SETTINGS_ACTION_HEIGHT
        self.draw_button(
            45,
            bottom_y,
            self.cx - 55,
            SETTINGS_ACTION_HEIGHT,
            "DONE",
            self.settings_focus == SETTINGS_DONE,
        )
        self.draw_button(
            self.cx + 10,
            bottom_y,
            self.cx - 55,
            SETTINGS_ACTION_HEIGHT,
            "EXIT",
            self.settings_focus == SETTINGS_EXIT,
        )
        self.draw_centered(
            self.cx,
            self.h - 6,
            "ARROWS MOVE/ADJUST   ENTER SELECT   ESC DONE",
            gfx.FONT_MONO_12,
            6,
            gfx.BLACK,
        )

    def draw(self, now_ms):
        if self.popup is not None:
            self.draw_popup()
        elif self.settings:
            self.draw_settings()
        else:
            self.draw_ring(now_ms)
        gfx.present()
        self.dirty = False
        self.selection_update = False
        self.selection_previous = None
        self.preview_dirty = False
        self.last_draw_ms = now_ms

    def run(self):
        device_input.clear()
        while self.running and not solaros.should_exit():
            now_ms = solaros.time.uptime_ms()
            self.retry_pending_start(now_ms)
            self.poll_hid()
            self.refresh_hid_status(now_ms)

            event = device_input.read(20)
            while event is not None:
                if event.get("type") == "pointer":
                    self.handle_pointer(event, now_ms)
                elif event.get("type") == "axis" and self.popup is None and not self.settings:
                    self.handle_axis(event, now_ms)
                event = device_input.read(0)

            self.handle_key(gfx.getch(0), now_ms)
            if self.auto_trigger and self.dwell.ready(now_ms):
                self.trigger(now_ms)

            if (
                self.trigger_flash_until
                and now_ms >= self.trigger_flash_until
            ):
                previous = self.trigger_flash_direction
                self.trigger_flash_direction = None
                self.trigger_flash_until = 0
                if previous is not None:
                    # A complete redraw is the reliable way to remove the
                    # inverted bitmap fill on compact 1bpp hex layouts.
                    self.dirty = True

            if self.notice and now_ms >= self.notice_until:
                self.notice = ""
                self.dirty = True
            if self.dirty:
                self.draw(now_ms)
            elif self.selection_update or self.preview_dirty:
                self.draw_partial(now_ms)

    def shutdown(self):
        self.persist()
        self.stop_hid()


def main():
    stored = load_config()
    parsed = parse_arguments(sys.argv, stored["auto"], stored["dwell"])
    if parsed is None:
        return
    auto_trigger, dwell_ms, request_pairing = parsed
    ble = getattr(solaros, "ble", None)
    if getattr(ble, "hid", None) is None:
        print("Hex-O-Spell requires firmware with BLE HID support.")
        return
    try:
        # status() reports the current boot, rather than the saved boot preference.
        if "disabled for this boot" in ble.status().lower():
            print("BLE is disabled. Run 'ble enable', then reboot before starting Hex-O-Spell.")
            return
    except OSError as exc:
        print("Hex-O-Spell could not read BLE status: " + str(exc))
        return
    app = None
    gfx_started = False
    try:
        solaros.tick_interval(TICK_INTERVAL_MS)
        gfx.begin()
        gfx_started = True
        app = HexOSpellApp(
            auto_trigger, dwell_ms, stored["paired"], request_pairing
        )
        app.run()
    finally:
        if app is not None:
            app.shutdown()
        if gfx_started:
            gfx.end()


main()
