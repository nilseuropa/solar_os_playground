from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "apps" / "hex-o-spell" / "hex_o_spell_core.py"
SPEC = importlib.util.spec_from_file_location("hex_o_spell_core", CORE_PATH)
CORE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CORE)


def load_app_module():
    fake_gfx = types.SimpleNamespace(
        KEY_ESCAPE=27,
        KEY_UP=128,
        KEY_DOWN=129,
        KEY_LEFT=130,
        KEY_RIGHT=131,
    )
    fake_input = types.SimpleNamespace(
        SOURCE_JOYSTICK=4,
        MODE_ABSOLUTE=0,
        MODE_RELATIVE=1,
        ACTION_MOVE=0,
        ACTION_PRESS=1,
        ACTION_RELEASE=2,
        AXIS_X=0,
        AXIS_Y=1,
    )
    fake_solaros = types.ModuleType("solaros")
    fake_solaros.gfx = fake_gfx
    fake_solaros.input = fake_input
    app_path = ROOT / "apps" / "hex-o-spell" / "hex_o_spell.py"
    module = types.ModuleType("hex_o_spell_app_test")
    old_solaros = sys.modules.get("solaros")
    old_core = sys.modules.get("hex_o_spell_core")
    sys.modules["solaros"] = fake_solaros
    sys.modules["hex_o_spell_core"] = CORE
    try:
        source = app_path.read_text(encoding="utf-8")
        source_without_entry = source.rsplit("\nmain()", 1)[0]
        exec(compile(source_without_entry, app_path, "exec"), module.__dict__)
    finally:
        if old_solaros is None:
            del sys.modules["solaros"]
        else:
            sys.modules["solaros"] = old_solaros
        if old_core is None:
            del sys.modules["hex_o_spell_core"]
        else:
            sys.modules["hex_o_spell_core"] = old_core
    return module, fake_input


class HexOSpellCoreTest(unittest.TestCase):
    def test_disabled_ble_exits_before_graphics_or_hid_start(self) -> None:
        for arguments in (["hex_o_spell.py"], ["hex_o_spell.py", "--pair"]):
            with self.subTest(arguments=arguments):
                module, _ = load_app_module()
                hid = types.SimpleNamespace(start=Mock(), pair=Mock())
                module.solaros.ble = types.SimpleNamespace(
                    hid=hid, status=lambda: "disabled for this boot"
                )
                module.gfx.begin = Mock()
                with patch.object(sys, "argv", arguments), \
                        patch("builtins.print") as output:
                    module.main()
                module.gfx.begin.assert_not_called()
                hid.start.assert_not_called()
                hid.pair.assert_not_called()
                self.assertIn("ble enable", output.call_args.args[0])
                self.assertIn("reboot", output.call_args.args[0])

    def test_missing_ble_api_exits_before_graphics(self) -> None:
        module, _ = load_app_module()
        module.gfx.begin = Mock()
        with patch.object(sys, "argv", ["hex_o_spell.py"]), \
                patch("builtins.print") as output:
            module.main()
        module.gfx.begin.assert_not_called()
        self.assertIn("requires", output.call_args.args[0])

    def test_enabled_ble_runs_and_releases_graphics(self) -> None:
        module, _ = load_app_module()
        module.solaros.ble = types.SimpleNamespace(hid=object(), status=lambda: "idle")
        module.solaros.tick_interval = Mock()
        module.gfx.begin = Mock()
        module.gfx.end = Mock()
        app = Mock()
        with patch.object(sys, "argv", ["hex_o_spell.py"]), \
                patch.object(module, "HexOSpellApp", return_value=app):
            module.main()
        module.gfx.begin.assert_called_once_with()
        app.run.assert_called_once_with()
        app.shutdown.assert_called_once_with()
        module.gfx.end.assert_called_once_with()

    def test_touch_activation_selects_and_triggers_immediately(self) -> None:
        state = CORE.HexOSpellState()
        self.assertIsNone(state.activate(0))
        self.assertEqual(state.level, "characters")
        self.assertEqual(state.selection, 0)

        self.assertEqual(state.activate(3), "C")
        self.assertEqual(state.selection, 3)

    def test_joystick_highlight_follows_coordinates_and_neutral(self) -> None:
        self.assertIsNone(CORE.joystick_direction(0, 0, 10000))
        self.assertIsNone(CORE.joystick_direction(9999, 0, 10000))
        self.assertEqual(CORE.joystick_direction(10000, 0, 10000), 1)
        self.assertEqual(CORE.joystick_direction(0, -20000, 10000), 3)

    def test_two_stage_selection_keeps_group_open(self) -> None:
        state = CORE.HexOSpellState()
        state.select(0)
        self.assertIsNone(state.trigger())
        self.assertEqual(state.level, "characters")
        self.assertEqual(state.selection, 0)

        state.select(3)
        self.assertEqual(state.trigger(), "C")
        self.assertEqual(state.selection, 3)
        state.select(1)
        self.assertEqual(state.trigger(), "A")
        self.assertEqual(state.level, "characters")

    def test_return_space_and_backspace(self) -> None:
        state = CORE.HexOSpellState()
        state.select(5)
        state.trigger()
        state.select(4)
        self.assertEqual(state.trigger(), " ")
        state.select(3)
        self.assertEqual(state.trigger(), "\b")
        state.select(0)
        self.assertIsNone(state.trigger())
        self.assertEqual(state.level, "groups")

    def test_direction_mapping_matches_browser_model(self) -> None:
        self.assertEqual(CORE.nearest_direction(0, 100), 0)
        self.assertEqual(CORE.nearest_direction(100, 100), 1)
        self.assertEqual(CORE.nearest_direction(100, -100), 2)
        self.assertEqual(CORE.nearest_direction(0, -100), 3)
        self.assertEqual(CORE.nearest_direction(-100, -100), 4)
        self.assertEqual(CORE.nearest_direction(-100, 100), 5)

    def test_keyboard_direction_cycle_wraps_from_both_ends(self) -> None:
        self.assertEqual(CORE.cycle_direction(None, 1), 0)
        self.assertEqual(CORE.cycle_direction(None, -1), 5)
        self.assertEqual(CORE.cycle_direction(5, 1), 0)
        self.assertEqual(CORE.cycle_direction(0, -1), 5)
        with self.assertRaises(ValueError):
            CORE.cycle_direction(0, 0)

    def test_hex_hit_test_rejects_gaps_and_accepts_edges(self) -> None:
        radius = 58
        half_height = 50
        self.assertTrue(CORE.point_in_hex(0, 0, radius, half_height))
        self.assertTrue(CORE.point_in_hex(radius, 0, radius, half_height))
        self.assertTrue(CORE.point_in_hex(radius // 2, half_height, radius, half_height))
        self.assertFalse(CORE.point_in_hex(radius, half_height, radius, half_height))
        self.assertFalse(CORE.point_in_hex(radius + 1, 0, radius, half_height))

    def test_settings_focus_covers_rows_and_bottom_buttons(self) -> None:
        focus = CORE.SETTINGS_AUTO
        focus = CORE.move_settings_focus(focus, "down")
        self.assertEqual(focus, CORE.SETTINGS_DWELL)
        focus = CORE.move_settings_focus(focus, "down")
        self.assertEqual(focus, CORE.SETTINGS_PAIR)
        focus = CORE.move_settings_focus(focus, "down")
        self.assertEqual(focus, CORE.SETTINGS_DONE)
        focus = CORE.move_settings_focus(focus, "right")
        self.assertEqual(focus, CORE.SETTINGS_EXIT)
        focus = CORE.move_settings_focus(focus, "up")
        self.assertEqual(focus, CORE.SETTINGS_PAIR)
        self.assertEqual(
            CORE.move_settings_focus(CORE.SETTINGS_AUTO, "up"),
            CORE.SETTINGS_AUTO,
        )

    def test_settings_footer_has_its_own_centered_strip(self) -> None:
        module, _ = load_app_module()
        texts = []
        module.gfx = types.SimpleNamespace(
            WHITE=3,
            BLACK=0,
            FONT_BOLD_18=18,
            FONT_MONO_12=12,
            clear=lambda color: None,
            font=lambda font: None,
            color=lambda color: None,
            text=lambda x, y, value: texts.append((x, y, value)),
        )
        app = module.HexOSpellApp.__new__(module.HexOSpellApp)
        app.w = 400
        app.h = 300
        app.cx = 200
        app.auto_trigger = False
        app.settings_focus = CORE.SETTINGS_PAIR
        app.dwell = types.SimpleNamespace(duration_ms=1000)
        buttons = []
        app.draw_button = lambda *args: buttons.append(args)

        app.draw_settings()

        title = next(item for item in texts if item[2] == "HEX-O-SPELL SETTINGS")
        footer = next(item for item in texts if item[2].startswith("ARROWS MOVE"))
        self.assertEqual(title[:2], (110, 30))
        self.assertEqual(footer[:2], (68, 294))
        for button in buttons[-2:]:
            self.assertEqual(button[1:4], (228, 145, 44))
            self.assertLessEqual(button[1] + button[3], footer[1] - 12)

    def test_settings_footer_is_not_a_touch_target(self) -> None:
        module, _ = load_app_module()
        app = module.HexOSpellApp.__new__(module.HexOSpellApp)
        app.h = 300
        app.cx = 200
        app.settings_focus = CORE.SETTINGS_PAIR
        app.dirty = False
        activated = []
        app.activate_setting = lambda: activated.append(app.settings_focus)
        app.adjust_dwell = lambda amount: None

        app.handle_settings_touch(100, 270)
        self.assertEqual(activated, [CORE.SETTINGS_DONE])
        app.handle_settings_touch(100, 285)
        self.assertEqual(activated, [CORE.SETTINGS_DONE])

    def test_pairing_popup_title_is_centered(self) -> None:
        module, _ = load_app_module()
        texts = []
        module.gfx = types.SimpleNamespace(
            WHITE=3,
            BLACK=0,
            FONT_BOLD_14=14,
            FONT_BOLD_18=18,
            FONT_MONO_12=12,
            clear=lambda color: None,
            font=lambda font: None,
            color=lambda color: None,
            text=lambda x, y, value: texts.append((x, y, value)),
            rect=lambda x, y, width, height: None,
        )
        app = module.HexOSpellApp.__new__(module.HexOSpellApp)
        app.w = 400
        app.h = 300
        app.cx = 200
        app.popup = "unpaired"
        app.passkey = None
        app.start_pending = False
        app.error = ""
        app.draw_button = lambda *args: None

        app.draw_popup()

        title = next(item for item in texts if item[2] == "BLE KEYBOARD")
        self.assertEqual(title[:2], (146, 77))

    def test_dwell_fires_once_until_rearmed(self) -> None:
        dwell = CORE.DwellTrigger(800)
        self.assertTrue(dwell.arm("joystick", 2, 100))
        self.assertFalse(dwell.ready(899))
        self.assertTrue(dwell.ready(900))
        dwell.lock()
        self.assertFalse(dwell.ready(2000))
        self.assertFalse(dwell.arm("joystick", 2, 2100))
        dwell.cancel("joystick")
        self.assertTrue(dwell.arm("joystick", 2, 2200))
        self.assertTrue(dwell.ready(3000))

    def test_pointer_hover_persists_and_press_triggers(self) -> None:
        module, device_input = load_app_module()
        app = module.HexOSpellApp.__new__(module.HexOSpellApp)
        app.w = 100
        app.h = 100
        app.cx = 50
        app.cy = 50
        app.cell_radius = 12
        app.cell_half_height = 10
        app.centers = [(50, 80), (76, 65), (76, 35), (50, 20), (24, 35), (24, 65)]
        app.state = CORE.HexOSpellState()
        app.dwell = CORE.DwellTrigger(1000)
        app.auto_trigger = False
        app.selection_source = None
        app.selection_update = False
        app.selection_previous = None
        app.dirty = False
        app.pointer_x = 50
        app.pointer_y = 50
        app.popup = None
        app.settings = False
        triggered = []
        app.trigger = lambda now_ms: triggered.append((app.state.selection, now_ms))

        event = {
            "mode": device_input.MODE_ABSOLUTE,
            "action": device_input.ACTION_MOVE,
            "x": 50,
            "y": 80,
        }
        app.handle_pointer(event, 100)
        self.assertEqual(app.state.selection, 0)
        self.assertEqual(triggered, [])

        event["action"] = device_input.ACTION_RELEASE
        app.handle_pointer(event, 110)
        self.assertEqual(app.state.selection, 0)

        event["action"] = device_input.ACTION_PRESS
        app.handle_pointer(event, 120)
        self.assertEqual(triggered, [(0, 120)])

        event.update(action=device_input.ACTION_MOVE, x=0, y=99)
        app.handle_pointer(event, 130)
        self.assertIsNone(app.state.selection)

    def test_joystick_neutral_clears_hover(self) -> None:
        module, device_input = load_app_module()
        app = module.HexOSpellApp.__new__(module.HexOSpellApp)
        app.state = CORE.HexOSpellState()
        app.dwell = CORE.DwellTrigger(1000)
        app.auto_trigger = False
        app.selection_source = None
        app.selection_update = False
        app.selection_previous = None
        app.dirty = False
        app.joystick_x = 0
        app.joystick_y = 0

        app.handle_axis(
            {
                "source_class": device_input.SOURCE_JOYSTICK,
                "axis": device_input.AXIS_X,
                "value": 20000,
            },
            100,
        )
        self.assertEqual(app.state.selection, 1)
        app.handle_axis(
            {
                "source_class": device_input.SOURCE_JOYSTICK,
                "axis": device_input.AXIS_X,
                "value": 0,
            },
            110,
        )
        self.assertIsNone(app.state.selection)

    def test_keyboard_arrows_cycle_and_space_enter_trigger(self) -> None:
        module, _ = load_app_module()
        app = module.HexOSpellApp.__new__(module.HexOSpellApp)
        app.popup = None
        app.settings = False
        app.running = True
        app.state = CORE.HexOSpellState()
        selected = []
        triggered = []

        def select(direction, source, now_ms):
            app.state.select(direction)
            selected.append((direction, source, now_ms))

        app.select_direction = select
        app.trigger = lambda now_ms: triggered.append(now_ms)

        app.handle_key(module.gfx.KEY_LEFT, 100)
        app.handle_key(module.gfx.KEY_LEFT, 110)
        app.handle_key(module.gfx.KEY_RIGHT, 120)
        app.handle_key(module.gfx.KEY_UP, 130)
        app.handle_key(module.gfx.KEY_DOWN, 140)
        app.handle_key(ord("z"), 150)
        app.handle_key(32, 160)
        app.handle_key(13, 170)

        self.assertEqual(
            selected,
            [
                (0, "keyboard", 100),
                (1, "keyboard", 110),
                (0, "keyboard", 120),
                (1, "keyboard", 130),
                (0, "keyboard", 140),
            ],
        )
        self.assertEqual(triggered, [160, 170])

    def test_hover_mask_is_one_hollow_band(self) -> None:
        module, _ = load_app_module()
        app = module.HexOSpellApp.__new__(module.HexOSpellApp)
        app.cell_radius = 12
        app.cell_half_height = 10

        solid = app.make_hex_fill_chunks()
        hover = app.make_hex_fill_chunks(module.HOVER_STROKE)
        solid_bits = sum(byte.bit_count() for _, _, data in solid for byte in data)
        hover_bits = sum(byte.bit_count() for _, _, data in hover for byte in data)

        self.assertGreater(hover_bits, 0)
        self.assertLess(hover_bits, solid_bits)

        row_bytes = (app.cell_radius * 2 + 8) // 8
        center_row = b""
        for start, rows, data in hover:
            if start <= 10 < start + rows:
                row_offset = (10 - start) * row_bytes
                center_row = data[row_offset : row_offset + row_bytes]
                break
        center = app.cell_radius
        self.assertEqual(center_row[center // 8] & (1 << (center & 7)), 0)
        self.assertNotEqual(center_row[0] & 1, 0)


if __name__ == "__main__":
    unittest.main()
