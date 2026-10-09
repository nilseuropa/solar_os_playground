import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


APP_PATH = Path(__file__).resolve().parents[1] / "apps/ble-keyboard/ble_keyboard.py"


class Keyboard:
    def __init__(self):
        self.calls = []
        self.held = set()
        self.fail = False
        self.release_failures = 0

    def press(self, *keys):
        assert 1 <= len(keys) <= 8
        if self.fail:
            raise OSError("queue full")
        self.held.update(keys)
        self.calls.append(("down", keys))

    def release(self, *keys):
        assert 1 <= len(keys) <= 8
        self.held.difference_update(keys)
        self.calls.append(("up", keys))

    def release_all(self):
        if self.release_failures:
            self.release_failures -= 1
            raise OSError("busy")
        self.held.clear()
        self.calls.append(("clear", ()))


class BleKeyboardTest(unittest.TestCase):
    def setUp(self):
        self.clock = 0
        self.captures = []
        self.releases = []
        self.ble_calls = []
        self.events = []
        self.start_failures = 0
        self.frames = []
        self.screen = (10, 32)
        self.keyboard = Keyboard()
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        self.writes = []
        def write_file(path, value):
            self.writes.append(path)
            Path(path).write_text(value)
        self.storage = types.SimpleNamespace(
            resolve=lambda path: self.data.name + path,
            exists=lambda path: Path(path).exists(),
            mkdir=lambda path: Path(path).mkdir(),
            read_file=lambda path, size: Path(path).read_bytes()[:size],
            write_file=write_file,
            rename=lambda source, destination: Path(source).rename(destination),
            remove=lambda path: Path(path).unlink())
        self.hosts = [dict(address="01:02:03:04:05:06", addr_type=0, connected=False),
                      dict(address="11:12:13:14:15:16", addr_type=1, connected=False)]
        self.devices = [
            dict(source=9, name="pointer0", ready=True, source_class=2, capabilities=2),
            dict(source=1, name="keyboard0", ready=True, source_class=1, capabilities=1),
            dict(source=2, name="keyboard1", ready=False, source_class=1, capabilities=1),
        ]
        self.status = dict(connected=True, encrypted=True, keyboard_subscribed=True,
                           bonded=True)
        self.input = types.SimpleNamespace(
            SOURCE_KEYBOARD=1, CAP_KEY_EVENTS=1,
            KEY_PRESS=0, KEY_RELEASE=1, KEY_REPEAT=2, MOD_CTRL=0x11, MOD_ALT=0x44,
            sources=lambda: self.devices,
            capture_keyboard=self.captures.append,
            release_keyboard=lambda: self.releases.append(True),
            read_key=lambda: None)
        self.hid = types.SimpleNamespace(
            KEY_DELETE=0x4c, KEY_A=4, KEY_TAB=0x2b, KEY_ESCAPE=0x29,
            KEY_LEFT_CTRL=0x101, KEY_LEFT_SHIFT=0x102,
            KEY_LEFT_ALT=0x104, KEY_LEFT_GUI=0x108,
            KEY_RIGHT_CTRL=0x110, KEY_RIGHT_SHIFT=0x120,
            KEY_RIGHT_ALT=0x140, KEY_RIGHT_GUI=0x180,
            keyboard=self.keyboard,
            start=self.start, stop=lambda: self.ble_calls.append("stop"),
            pair=self.pair,
            hosts=lambda: [host.copy() for host in self.hosts],
            connect=lambda address, kind: self.ble_calls.append(("connect", address, kind)),
            forget=self.forget,
            disconnect=lambda: self.ble_calls.append("disconnect"),
            poll=lambda: self.events.pop(0) if self.events else None,
            status=lambda: self.status.copy())
        self.tui = types.SimpleNamespace(
            KEY_UP=128, KEY_DOWN=129, KEY_ESCAPE=27, NORMAL=0, BOLD=1, INVERSE=2,
            size=lambda: self.screen, clear=lambda: self.frames.clear(),
            addstr=self.addstr, refresh=lambda: None, getch=lambda timeout: None)
        self.runtime = types.SimpleNamespace(
            storage=self.storage,
            input=self.input, ble=types.SimpleNamespace(hid=self.hid, status=lambda: "idle"),
            tui=self.tui, time=types.SimpleNamespace(uptime_ms=lambda: self.clock,
                                                   sleep_ms=self.sleep),
            tick_interval=lambda ms: None, should_exit=lambda: False)
        self.module = types.ModuleType("ble_keyboard_test")
        source = APP_PATH.read_text().rsplit("\nmain()", 1)[0]
        with patch.dict(sys.modules, solaros=self.runtime):
            exec(compile(source, str(APP_PATH), "exec"), self.module.__dict__)
        self.app = self.module.KeyboardApp()
        self.app.source = self.app.devices[0]

    def start(self, name, manual=False):
        if self.start_failures:
            self.start_failures -= 1
            raise OSError("closing")
        self.ble_calls.append(("start", name, manual))

    def sleep(self, ms):
        self.clock += ms

    def addstr(self, row, col, text, attr):
        self.assertLess(row, self.screen[0])
        self.assertLessEqual(col + len(text), self.screen[1])
        self.frames.append((row, text, attr))

    def event(self, usage, action=0, modifiers=0, source=1, physical=None, key=0):
        self.app.key_event(dict(type="key", source=source, usage=usage,
                                physical_key=usage if physical is None else physical,
                                key=key, action=action, modifiers=modifiers))

    def ready(self):
        self.app.started = True
        self.app.ready = True

    def pair(self):
        if self.start_failures:
            self.start_failures -= 1
            raise OSError("busy")
        self.ble_calls.append("pair")

    def forget(self, address, kind):
        self.ble_calls.append(("forget", address, kind))
        self.hosts = [host for host in self.hosts if
                      (host["address"], host["addr_type"]) != (address, kind)]

    def test_tab_switches_focus_and_arrows_stay_in_the_focused_list(self):
        self.devices[2]["ready"] = True
        self.app.refresh_devices()
        self.app.refresh_hosts()
        self.app.setup_key(self.tui.KEY_DOWN)
        self.assertEqual(self.app.selected, 1)
        self.assertEqual(self.app.source["name"], "keyboard0")
        self.app.setup_key(13)
        self.assertEqual(self.app.source["name"], "keyboard1")
        self.app.setup_key(9)
        self.app.setup_key(self.tui.KEY_DOWN)
        self.assertEqual(self.app.host_index, 1)
        self.assertEqual(self.app.selected, 1)
        self.app.setup_key(13)
        self.app.poll(0)
        self.assertIn(("connect", "11:12:13:14:15:16", 1), self.ble_calls)
        self.assertNotIn("pair", self.ble_calls)
        self.app.setup_key(9)
        self.assertEqual(self.app.focus, "input")

    def test_names_persist_in_hidden_directory_and_unchanged_polls_do_not_write(self):
        self.hosts[0]["name"] = "Wintermute"
        self.app.refresh_hosts()
        path = Path(self.app.host_names.path)
        self.assertEqual(path.parent.name, ".ble-keyboard")
        self.assertEqual(path.name, "hosts.json")
        self.assertEqual(len(self.writes), 1)
        self.app.refresh_hosts()
        self.assertEqual(len(self.writes), 1)
        self.hosts[0].pop("name")
        restarted = self.module.KeyboardApp()
        restarted.refresh_hosts()
        self.assertEqual(restarted.host_names.display(restarted.hosts[0]), "Wintermute")
        self.assertEqual(len(self.writes), 1)

    def test_delayed_native_name_is_cached_during_forwarding_without_repeated_host_reads(self):
        self.app.refresh_hosts()
        self.app.started = True
        with patch.object(self.hid, "hosts", wraps=self.hid.hosts) as reads:
            self.app.poll(0)
            self.assertTrue(self.app.forwarding)
            self.assertEqual(reads.call_count, 0)
            self.hosts[0]["name"] = "Desktop"
            self.status["host_name"] = "Desktop"
            self.app.poll(300)
            self.assertEqual(reads.call_count, 1)
            self.assertEqual(self.app.host_names.display(self.hosts[0]), "Desktop")
            self.app.poll(600)
            self.assertEqual(reads.call_count, 1)
            self.assertEqual(len(self.writes), 1)
            self.assertTrue(self.app.forwarding)

    def test_manual_label_survives_discovery_updates_and_empty_restores_automatic_name(self):
        self.hosts[0]["name"] = "Phone"
        self.app.refresh_hosts()
        self.app.host_names.label(self.hosts[0], "My phone")
        self.hosts[0]["name"] = "Android"
        self.app.refresh_hosts()
        self.assertEqual(self.app.host_names.display(self.hosts[0]), "My phone")
        self.app.host_names.label(self.hosts[0], "")
        self.assertEqual(self.app.host_names.display(self.hosts[0]), "Android")
        restarted = self.module.KeyboardApp()
        self.assertEqual(restarted.host_names.display(self.hosts[0]), "Android")

    def test_public_and_random_host_identities_have_separate_names(self):
        self.hosts[1].update(address=self.hosts[0]["address"], name="Random host")
        self.hosts[0]["name"] = "Public host"
        self.app.refresh_hosts()
        self.assertEqual(self.app.host_names.display(self.hosts[0]), "Public host")
        self.assertEqual(self.app.host_names.display(self.hosts[1]), "Random host")

    def test_rename_is_modal_and_defers_capture_if_host_connects_during_edit(self):
        self.app.refresh_hosts()
        self.app.setup_key(9)
        self.app.setup_key(ord("N"))
        for c in "PhonePId":
            self.app.setup_key(ord(c))
        self.app.started = True
        self.app.poll(0)
        self.assertFalse(self.app.captured)
        self.assertFalse(self.ble_calls)
        self.app.setup_key(8)
        self.app.setup_key(13)
        self.assertEqual(self.app.host_names.display(self.hosts[0]), "PhonePI")
        self.app.poll(300)
        self.assertTrue(self.app.captured)

    def test_rename_cancel_keeps_name_and_empty_unknown_label_restores_address(self):
        self.app.refresh_hosts()
        self.app.focus = "hosts"
        self.app.setup_key(ord("N"))
        self.app.setup_key(ord("x"))
        self.app.setup_key(self.tui.KEY_ESCAPE)
        self.assertFalse(self.app.host_names.records)
        self.assertTrue(self.app.running)
        self.app.host_names.label(self.hosts[0], "Temporary")
        self.app.host_names.label(self.hosts[0], "")
        self.assertEqual(self.app.host_names.display(self.hosts[0]), self.hosts[0]["address"])

    def test_delete_pairing_removes_its_name_only_after_successful_bond_deletion(self):
        self.hosts[0]["name"] = "Phone"
        self.app.refresh_hosts()
        self.app.focus = "hosts"
        with patch.object(self.hid, "forget", side_effect=OSError("busy")):
            self.app.setup_key(ord("d"))
        self.assertTrue(self.app.host_names.records)
        self.app.setup_key(ord("d"))
        self.assertFalse(self.app.host_names.records)
        self.assertEqual(len(self.hosts), 1)

    def test_corrupt_primary_recovers_backup_and_damaged_data_keeps_app_usable(self):
        names = self.app.host_names
        names.label(self.hosts[0], "Phone")
        path = Path(names.path)
        backup = Path(names.path + ".bak")
        backup.write_bytes(path.read_bytes())
        path.write_text("not json")
        restarted = self.module.KeyboardApp()
        self.assertEqual(restarted.host_names.display(self.hosts[0]), "Phone")
        self.assertTrue(any("Recovered" in line for line in restarted.log))
        backup.write_text("[]")
        damaged = self.module.KeyboardApp()
        self.assertFalse(damaged.host_names.records)
        self.assertTrue(any("unavailable" in line for line in damaged.log))
        self.assertTrue(path.exists())

    def test_failed_promotion_preserves_committed_file_and_does_not_retry_every_poll(self):
        self.hosts[0]["name"] = "Before"
        self.app.refresh_hosts()
        committed = Path(self.app.host_names.path).read_bytes()
        rename = self.storage.rename
        def fail_promotion(source, destination):
            if source.endswith(".tmp"):
                raise OSError("read-only")
            return rename(source, destination)
        self.hosts[0]["name"] = "After"
        with patch.object(self.storage, "rename", side_effect=fail_promotion):
            self.app.refresh_hosts()
            self.app.refresh_hosts()
        self.assertEqual(Path(self.app.host_names.path).read_bytes(), committed)
        self.assertEqual(len(self.writes), 2)
        self.assertEqual(self.app.host_names.display(self.hosts[0]), "After")
        self.assertTrue(any("not saved" in line for line in self.app.log))

    def test_unicode_names_are_cached_and_terminal_control_characters_are_removed(self):
        self.hosts[0]["name"] = " Téléphone\n\x1b\x7f "
        self.app.refresh_hosts()
        self.assertEqual(self.app.host_names.display(self.hosts[0]), "Téléphone")
        self.app.host_names.label(self.hosts[0], "x" * 100)
        self.assertEqual(len(self.app.host_names.display(self.hosts[0])), self.module.MAX_NAME)

    def test_oversized_name_file_is_rejected_and_missing_storage_is_nonfatal(self):
        names = self.app.host_names
        Path(names.directory).mkdir()
        Path(names.path).write_bytes(b" " * (self.module.MAX_NAMES_FILE + 1))
        restarted = self.module.KeyboardApp()
        self.assertFalse(restarted.host_names.records)
        self.runtime.storage = None
        no_storage = self.module.KeyboardApp()
        no_storage.host_names.label(self.hosts[0], "RAM only")
        self.assertEqual(no_storage.host_names.display(self.hosts[0]), "RAM only")
        self.assertTrue(any("not saved" in line for line in no_storage.log))

    def test_delete_only_applies_to_the_host_list_and_I_rescans_inputs(self):
        self.app.refresh_hosts()
        self.app.setup_key(ord("d"))
        self.assertEqual(len(self.hosts), 2)
        self.app.setup_key(9)
        self.app.setup_key(ord("d"))
        self.assertEqual(len(self.hosts), 1)
        self.assertEqual(self.app.hosts, self.hosts)
        self.devices[2]["ready"] = True
        self.app.setup_key(ord("I"))
        self.assertEqual(len(self.app.devices), 2)
        self.assertEqual(self.app.focus, "hosts")

    def test_no_implicit_pairing_or_host_connection_on_input_selection(self):
        self.assertEqual(self.app.source["name"], "keyboard0")
        self.app.begin_forwarding()
        self.assertFalse(self.app.captured)
        self.assertIsNone(self.app.pending)
        self.assertFalse(self.ble_calls)

    def test_start_is_idle_and_opens_the_lists_without_capturing(self):
        self.runtime.should_exit = lambda: True
        self.app.run()
        self.assertEqual(self.ble_calls, [("start", "SolarOS Keyboard", True)])
        self.assertEqual(len(self.app.hosts), 2)
        self.assertFalse(self.captures)

    def test_disabled_ble_exits_before_creating_app_even_with_pair_argument(self):
        self.runtime.ble.status = lambda: "disabled for this boot"
        with patch.object(sys, "argv", ["ble_keyboard.py", "--pair"]), \
                patch.object(self.module, "KeyboardApp") as create_app, \
                patch("builtins.print") as output:
            self.module.main()
        create_app.assert_not_called()
        self.assertFalse(self.ble_calls)
        self.assertFalse(self.captures)
        self.assertIn("ble enable", output.call_args.args[0])
        self.assertIn("reboot", output.call_args.args[0])

    def test_missing_ble_api_exits_cleanly(self):
        self.runtime.ble = None
        with patch.object(sys, "argv", ["ble_keyboard.py"]), \
                patch.object(self.module, "KeyboardApp") as create_app, \
                patch("builtins.print") as output:
            self.module.main()
        create_app.assert_not_called()
        self.assertIn("requires", output.call_args.args[0])

    def test_hid_start_failure_exits_without_capture_or_stop(self):
        self.start_failures = 1
        with patch.object(sys, "argv", ["ble_keyboard.py"]), \
                patch("builtins.print") as output:
            self.module.main()
        self.assertIn("could not start BLE HID", output.call_args.args[0])
        self.assertFalse(self.ble_calls)
        self.assertFalse(self.captures)
        self.assertFalse(self.releases)

    def test_controls_remain_available_until_all_host_ready_conditions_hold(self):
        self.app.started = True
        self.status.update(connected=False, encrypted=False, bonded=False,
                           keyboard_subscribed=False)
        self.app.setup_key(ord("P"))
        self.app.poll(0)
        self.assertIn("pair", self.ble_calls)
        self.assertFalse(self.app.captured)
        for index, field in enumerate(("connected", "encrypted", "bonded")):
            self.status[field] = True
            self.app.poll(300 + index * 300)
            self.assertFalse(self.app.captured)
        self.status["keyboard_subscribed"] = True
        self.app.poll(1200)
        self.assertEqual(self.captures, ["keyboard0"])
        self.assertTrue(self.app.forwarding)

    def test_disconnect_releases_capture_and_reconnect_recaptures(self):
        self.app.started = True
        self.app.poll(0)
        self.status["connected"] = False
        self.events.append(dict(type="disconnected", status=19))
        self.app.poll(300)
        self.assertFalse(self.app.captured)
        self.assertEqual(self.releases, [True])
        self.app.setup_key(9)
        self.assertEqual(self.app.focus, "hosts")
        self.status["connected"] = True
        self.app.poll(600)
        self.assertEqual(self.captures, ["keyboard0", "keyboard0"])

    def test_cancel_pending_request_does_not_start_pairing(self):
        self.app.started = True
        self.app.setup_key(ord("p"))
        self.app.setup_key(self.tui.KEY_ESCAPE)
        self.status.update(connected=False, encrypted=False, bonded=False)
        self.app.poll(300)
        self.assertIsNone(self.app.pending)
        self.assertIn("disconnect", self.ble_calls)
        self.assertNotIn("pair", self.ble_calls)
        self.assertFalse(self.captures)

    def test_ready_host_without_input_is_not_labelled_as_negotiating(self):
        self.app.ready = True
        self.app.status["connected"] = True
        self.app.source = None
        self.app.draw(0)
        self.assertIn("Host ready", self.frames[0][1])
        self.assertFalse(self.app.captured)

    def test_log_occupies_bottom_half_and_lists_are_both_visible(self):
        self.screen = (22, 60)
        self.app.refresh_hosts()
        for index in range(48):
            self.app.note("TX " + str(index))
        self.app.draw(1000)
        self.assertTrue(any("Inputs" in text for row, text, attr in self.frames))
        self.assertTrue(any("Paired hosts" in text for row, text, attr in self.frames))
        self.assertTrue(all(row > 11 for row, text, attr in self.frames if text.startswith("TX")))
        self.assertEqual(sum(text.startswith("TX") for row, text, attr in self.frames), 8)

    def test_press_release_and_host_repeat(self):
        self.ready()
        self.event(4)
        self.event(4, self.input.KEY_REPEAT)
        self.assertEqual(self.keyboard.calls, [("down", (4,))])
        self.event(4, self.input.KEY_RELEASE)
        self.assertEqual(self.keyboard.held, set())
        self.assertTrue(any(line.startswith("TX up") for line in self.app.log))

    def test_failed_security_is_logged_and_does_not_remember_host(self):
        self.app.started = True
        self.status.update(connected=False, encrypted=False, bonded=False,
                           keyboard_subscribed=False)
        self.events.extend([
            dict(type="connected"),
            dict(type="secured", status=0x405, bonded=True, encrypted=False),
            dict(type="disconnected", status=0x205),
        ])
        self.app.poll(1000)
        self.assertFalse(self.app.captured)
        self.assertIn("BLE security failed: 0x405", self.app.log)
        self.assertIn("BLE disconnect: 0x205", self.app.log)

    def test_raw_modifier_usages_use_typed_modifier_constants(self):
        self.ready()
        self.event(0xe2, modifiers=4)
        self.event(0x2b, modifiers=4)
        self.assertEqual(self.keyboard.held, {self.hid.KEY_LEFT_ALT, self.hid.KEY_TAB})
        self.event(0x2b, self.input.KEY_RELEASE, modifiers=4)
        self.event(0xe2, self.input.KEY_RELEASE)
        self.assertEqual(self.keyboard.held, set())
        self.assertNotIn(0xe2, [key for _, keys in self.keyboard.calls for key in keys])

    def test_escape_ctrl_bracket_and_focus_chords_are_forwarded(self):
        self.ready()
        for usage, modifiers in ((0x29, 0), (0x30, 1), (0x50, 5)):
            self.event(usage, modifiers=modifiers)
            self.assertIn(usage, self.keyboard.held)
            self.assertTrue(self.app.running)
            self.event(usage, self.input.KEY_RELEASE)

    def test_left_and_right_ctrl_alt_delete_quit_without_sending_delete(self):
        for modifiers in (5, 0x50, 0x41, 0x14):
            self.ready()
            self.app.running = True
            self.event(4)
            self.event(0x4c, modifiers=modifiers)
            self.assertFalse(self.app.running)
            self.assertEqual(self.keyboard.held, set())
            self.assertFalse(any(0x4c in keys for _, keys in self.keyboard.calls))

    def test_wrong_source_and_disconnected_keys_are_discarded(self):
        self.ready()
        self.event(4, source=2)
        self.assertFalse(self.keyboard.calls)
        self.app.ready = False
        self.event(4)
        self.app.ready = True
        self.event(4, self.input.KEY_REPEAT)
        self.event(4, self.input.KEY_RELEASE)
        self.assertFalse(self.keyboard.calls)

    def test_reset_disconnect_and_send_failure_neutralize_held_keys(self):
        self.ready()
        self.event(4)
        self.app.key_event(dict(type="reset"))
        self.assertFalse(self.keyboard.held)
        self.event(4)
        self.events.append(dict(type="disconnected"))
        self.status["connected"] = False
        self.app.poll(1000)
        self.assertFalse(self.keyboard.held)
        self.app.ready = True
        self.keyboard.fail = True
        self.event(4)
        self.assertFalse(self.app.held)
        self.assertTrue(any("TX failed" in line for line in self.app.log))

    def test_failed_neutral_report_blocks_input_until_release_retry_succeeds(self):
        self.ready()
        self.event(4)
        self.keyboard.release_failures = 1
        self.app.key_event(dict(type="reset"))
        self.assertTrue(self.app.release_pending)
        self.event(0x2b)
        self.assertEqual(self.keyboard.held, {4})
        self.app.release_keys()
        self.assertFalse(self.app.release_pending)
        self.assertFalse(self.keyboard.held)
        self.event(0x2b)
        self.assertEqual(self.keyboard.held, {0x2b})

    def test_pairing_request_retries_and_passkey_keeps_leading_zeros(self):
        self.app.started = True
        self.status.update(connected=False, encrypted=False, bonded=False)
        self.start_failures = 2
        self.app.begin_pairing()
        self.app.poll(0)
        self.app.poll(150)
        self.assertIsNotNone(self.app.pending)
        self.app.poll(300)
        self.assertIsNone(self.app.pending)
        self.events.append(dict(type="passkey", passkey=123))
        self.app.poll(600)
        self.app.draw(600)
        self.assertTrue(any("000123" in text for row, text, attr in self.frames))

    def test_full_modifier_reports_are_batched_to_binding_limit(self):
        self.ready()
        self.event(4, modifiers=255)
        self.assertEqual(len(self.keyboard.held), 9)
        self.event(4, self.input.KEY_RELEASE)
        self.assertFalse(self.keyboard.held)

    def test_draw_fits_compact_terminal_and_log_is_bounded(self):
        self.ready()
        self.app.forwarding = True
        for index in range(100):
            self.app.note("TX %d" % index)
        self.assertEqual(len(self.app.log), self.module.MAX_LOG)
        self.screen = (6, 24)
        self.app.draw(1000)
        self.assertIn("Ctrl+Alt+Del quit", self.frames[-1][1])

    def test_exception_shutdown_releases_capture_even_if_ble_stop_fails(self):
        self.ready()
        self.app.captured = True
        self.event(4)
        self.hid.stop = lambda: (_ for _ in ()).throw(OSError("stop failed"))
        with self.assertRaises(OSError):
            self.app.shutdown()
        self.assertEqual(self.releases, [True])
        self.assertFalse(self.keyboard.held)

    def test_run_exception_always_cleans_up_and_can_run_again(self):
        self.module.KeyboardApp = lambda: self.app
        self.app.captured = True
        self.ready()
        self.app.run = lambda pair: (_ for _ in ()).throw(RuntimeError("failed"))
        with patch.object(sys, "argv", ["ble_keyboard.py"]):
            with self.assertRaises(RuntimeError):
                self.module.main()
        self.assertFalse(self.app.started)
        self.assertEqual(self.releases, [True])
        self.module.KeyboardApp = type(self.app)
        second = self.module.KeyboardApp()
        second.started = True
        second.poll(1000)
        second.shutdown()
        self.assertEqual(self.captures, ["keyboard0"])
        self.assertEqual(len(self.releases), 2)



if __name__ == "__main__":
    unittest.main()
