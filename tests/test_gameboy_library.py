"""Network-contract, ROM installation, cache, and screenshot sizing checks."""

import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit


APP = Path(__file__).resolve().parents[1] / "apps/gameboy-library"
spec = importlib.util.spec_from_file_location("gameboy_library_core", APP / "library.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def entry(**changes):
    value = {"slug": "2048gb", "title": "2048gb", "platform": "GB",
             "typetag": "game", "developer": "Sanqui", "license": "Zlib",
             "tags": ["Puzzle"], "screenshots": ["1.png"],
             "files": [{"filename": "2048.gb", "default": True, "playable": True}]}
    value.update(changes)
    return value


def rom(cgb=0, cartridge=3):
    data = bytearray(32768)
    data[0x143] = cgb
    data[0x147] = cartridge
    data[0x14D] = (-sum(data[0x134:0x14D]) - 25) & 255
    return bytes(data)


class Storage:
    def __init__(self):
        self.fail_destination = None

    def makedirs(self, path):
        Path(path).mkdir(parents=True, exist_ok=True)

    def exists(self, path):
        return Path(path).exists()

    def remove(self, path):
        Path(path).unlink()

    def rename(self, source, destination):
        if self.fail_destination == destination and not source.endswith(".old"):
            raise OSError("simulated rename failure")
        if Path(destination).exists():
            raise OSError("cannot overwrite on this filesystem")
        Path(source).rename(destination)

    def scandir(self, path, cursor, limit):
        items = sorted(Path(path).iterdir())
        start = cursor or 0
        page = items[start:start + limit]
        return {"entries": [{"name": p.name, "is_dir": p.is_dir()} for p in page],
                "next_cursor": start + limit if start + limit < len(items) else None}


class HTTP:
    def __init__(self, document=None, payload=None, total=None, error=None):
        self.document = document
        self.payload = rom() if payload is None else payload
        self.total = len(self.payload) if total is None else total
        self.error = error
        self.calls = []
        self.closed = []
        self.events = []

    def get(self, *args):
        self.calls.append(args)
        if self.error:
            raise self.error
        return {"status_code": 200, "body": json.dumps(self.document).encode(),
                "truncated": False}

    def stream_open(self, *args):
        self.calls.append(args)
        self.events = [{"type": "response", "status_code": 200,
                        "content_length": self.total}]
        self.events += [{"type": "data", "data": self.payload[i:i + 1024]}
                        for i in range(0, len(self.payload), 1024)]
        self.events.append({"type": "complete"})
        return 7

    def stream_read(self, handle, timeout):
        return self.events.pop(0)

    def stream_close(self, handle):
        self.closed.append(handle)


class CatalogHTTP(HTTP):
    def __init__(self, records):
        super().__init__()
        self.records = records

    def get(self, *args):
        self.calls.append(args)
        if self.error:
            raise self.error
        query = parse_qs(urlsplit(args[0]).query)
        size = int(query["results"][0])
        pages = max(1, (len(self.records) + size - 1) // size)
        page = min(pages, int(query["page"][0]))
        document = {"entries": self.records[(page - 1) * size:page * size],
                    "page_current": page, "page_total": pages, "results": len(self.records)}
        return {"status_code": 200, "body": json.dumps(document).encode(), "truncated": False}


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = self.temporary.name
        self.storage = Storage()

    def client(self, http):
        return core.Library(http, self.storage, self.root, lambda: False, lambda: 0)

    def test_query_matches_current_api_and_encodes_search(self):
        url = core.search_url("a & ☀", "Card Game", 2)
        self.assertIn("platform=GB&typetag=game&results=8&page=2", url)
        self.assertIn("sort=title&order=asc", url)
        self.assertIn("q=a%20%26%20%E2%98%80", url)
        self.assertIn("tags=Card%20Game", url)

    def test_query_page_size_is_configurable_and_bounded(self):
        self.assertIn("results=7&page=2", core.search_url(page=2, page_size=7))
        self.assertIn("results=1&", core.search_url(page_size=0))
        self.assertIn("results=24&", core.search_url(page_size=100))

    def test_catalog_cache_and_response_limits_follow_the_requested_page_size(self):
        http = HTTP({"entries": [entry(slug="game-%d" % i) for i in range(8)],
                     "results": 21, "page_total": 3, "page_current": 1})
        client = self.client(http)
        self.assertEqual(len(client.page(page_size=8)["entries"]), 8)
        self.assertEqual(len(client.page(page_size=7)["entries"]), 7)
        self.assertEqual(len(http.calls), 2)
        self.assertIn("results=8&", http.calls[0][0])
        self.assertIn("results=7&", http.calls[1][0])
        http.error = OSError("offline")
        self.assertTrue(client.page(page_size=7)["cached"])
        self.assertEqual(len(http.calls), 2)

    def test_filtered_pages_fill_across_batches_without_losing_or_repeating_games(self):
        records = [entry(slug="game-%02d" % i) for i in range(26)]
        for index in (0, 5, 6, 11, 13, 14, 20):
            records[index]["files"] = []
        client = self.client(CatalogHTTP(records))
        expected = [record["slug"] for record in records if record["files"]]
        cursor, pages, cursors = None, [], []
        while True:
            cursors.append(cursor)
            page = client.filtered_page("name", "Puzzle", cursor, page_size=7)
            pages.append(page)
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual([len(page["entries"]) for page in pages], [7, 7, 5])
        self.assertEqual([item["slug"] for page in pages for item in page["entries"]], expected)
        for cursor, original in zip(reversed(cursors), reversed(pages)):
            again = client.filtered_page("name", "Puzzle", cursor, page_size=7)
            self.assertEqual(again["entries"], original["entries"])
            self.assertEqual(again["next_cursor"], original["next_cursor"])
        for call in client.http.calls:
            self.assertIn("q=name&tags=Puzzle", call[0])

    def test_filtered_last_full_page_does_not_advertise_an_empty_page(self):
        records = [entry(slug="game-%02d" % i) for i in range(21)]
        for record in records[7:]:
            record["files"] = []
        client = self.client(CatalogHTTP(records))
        page = client.filtered_page(page_size=7)
        self.assertEqual(len(page["entries"]), 7)
        self.assertIsNone(page["next_cursor"])

    def test_fully_filtered_catalog_is_one_empty_final_page(self):
        records = [entry(slug="game-%d" % i, files=[]) for i in range(16)]
        client = self.client(CatalogHTTP(records))
        self.assertEqual(client.filtered_page(page_size=7)["entries"], [])
        self.assertIsNone(client.filtered_page(page_size=7)["next_cursor"])

    def test_filtered_view_cache_works_offline_and_refresh_preserves_current_view(self):
        client = self.client(CatalogHTTP([entry(slug="game-%d" % i) for i in range(11)]))
        first = client.filtered_page(page_size=7)
        client.http.error = OSError("offline")
        self.assertTrue(client.filtered_page(page_size=7)["cached"])
        fallback = client.filtered_page(refresh=True, page_size=7)
        self.assertTrue(fallback["offline"])
        self.assertEqual(fallback["entries"], first["entries"])
        self.assertEqual(fallback["next_cursor"], first["next_cursor"])

    def test_refresh_rebuilds_filter_positions_after_upstream_changes(self):
        http = CatalogHTTP([entry(slug="game-%d" % i) for i in range(18)])
        client = self.client(http)
        old_first = client.filtered_page(page_size=7)
        client.filtered_page(cursor=old_first["next_cursor"], page_size=7)
        http.records[1]["files"] = []
        refreshed = client.filtered_page(refresh=True, page_size=7)
        second = client.filtered_page(cursor=refreshed["next_cursor"], page_size=7)
        self.assertEqual([item["slug"] for item in refreshed["entries"]],
                         ["game-0", "game-2", "game-3", "game-4", "game-5", "game-6", "game-7"])
        self.assertEqual(second["entries"][0]["slug"], "game-8")

    def test_asset_paths_cannot_leave_the_entry_or_change_origin(self):
        for slug, name in [("..", "game.gb"), ("a/b", "game.gb"),
                           ("valid", "../game.gb"), ("valid", "/game.gb"),
                           ("valid", "https://other/game.gb"), ("valid", "a\\b.gb")]:
            with self.subTest(slug=slug, name=name), self.assertRaises(ValueError):
                core.asset_url(slug, name)
        self.assertTrue(core.asset_url("valid", "shots/level 1.png").endswith(
            "/valid/shots/level%201.png"))
        self.assertTrue(core.asset_url("valid", "%2e%2e/game.gb").endswith(
            "/valid/%252e%252e/game.gb"))

    def test_metadata_accepts_author_variants_and_drops_unplayable_files(self):
        value = core.normalize_entry(entry(developer=[{"name": "A"}, "B"],
            files=[{"filename": "no.gbc"}, {"filename": "../bad.gb"},
                   {"filename": "debug.gb", "playable": False},
                   {"filename": "release.gb", "default": True}],
            screenshots=["bad.txt", "../shot.png", "1.png"]))
        self.assertEqual(value["developer"], "A, B")
        self.assertEqual(value["files"], [{"filename": "release.gb", "default": True}])
        self.assertEqual(value["screenshots"], ["1.png"])
        self.assertIsNone(core.normalize_entry(entry(platform="GBC")))
        self.assertIsNone(core.normalize_entry(entry(typetag="hackrom")))

    def test_catalog_page_and_refresh_fallback_work_offline(self):
        http = HTTP({"entries": [entry()], "results": 1, "page_total": 1,
                     "page_current": 1})
        client = self.client(http)
        first = client.page("2048", "Puzzle")
        self.assertEqual(first["entries"][0]["slug"], "2048gb")
        http.error = OSError("offline")
        self.assertTrue(client.page("2048", "Puzzle")["cached"])
        self.assertEqual(len(http.calls), 1)
        self.assertTrue(client.page("2048", "Puzzle", refresh=True)["offline"])
        with self.assertRaises(OSError):
            client.page("uncached")

    def test_successful_rom_is_validated_and_existing_rom_and_save_are_preserved(self):
        http = HTTP()
        client = self.client(http)
        value = core.normalize_entry(entry())
        path = client.download(value, 0, lambda *args: None, lambda: False)
        self.assertEqual(core.validate_rom(path), 32768)
        save = Path(path).with_suffix(".sav")
        save.write_bytes(b"save game")
        before = len(http.calls)
        client.download(value, 0, lambda *args: None, lambda: False)
        self.assertEqual(len(http.calls), before)
        self.assertEqual(save.read_bytes(), b"save game")
        self.assertEqual(client.downloads()[0]["title"], "2048gb")
        self.assertEqual(http.closed, [7])

    def test_incompatible_and_corrupt_downloads_never_become_installed_games(self):
        corrupt = bytearray(rom())
        corrupt[0x14D] ^= 1
        for payload in (rom(cgb=0xC0), rom(cartridge=0x22), bytes(corrupt), rom()[:400]):
            client = self.client(HTTP(payload=payload))
            value = core.normalize_entry(entry())
            with self.subTest(size=len(payload), flag=payload[0x143]), self.assertRaises(ValueError):
                client.download(value, 0, lambda *args: None, lambda: False)
            self.assertFalse(Path(client.rom_path(value)).exists())
            self.assertFalse(Path(client.rom_path(value) + ".part").exists())

    def test_cancellation_truncation_and_size_limits_release_stream_and_partial_file(self):
        for http, cancel, error in [(HTTP(), lambda: True, core.Cancelled),
                                     (HTTP(total=40000), lambda: False, OSError),
                                     (HTTP(total=core.ROM_LIMIT + 1), lambda: False, ValueError)]:
            client = self.client(http)
            path = self.root + "/test.part"
            with self.subTest(error=error), self.assertRaises(error):
                client.transfer("https://example.test/file", path, core.ROM_LIMIT,
                                lambda *args: None, cancel)
            self.assertFalse(Path(path).exists())
            self.assertEqual(http.closed, [7])

    def test_failed_atomic_replace_restores_previous_file(self):
        client = self.client(HTTP())
        path, temporary = self.root + "/cache.json", self.root + "/cache.json.part"
        Path(path).write_bytes(b"original")
        Path(temporary).write_bytes(b"replacement")
        self.storage.fail_destination = path
        with self.assertRaises(OSError):
            client.replace(temporary, path)
        self.assertEqual(Path(path).read_bytes(), b"original")

    def test_screenshot_transfer_is_cached_and_keeps_encoded_asset_path(self):
        http = HTTP(payload=b"png fixture")
        client = self.client(http)
        value = core.normalize_entry(entry(screenshots=["shot 1.png"]))
        path = client.screenshot(value, 0, lambda *args: None, lambda: False)
        self.assertEqual(Path(path).read_bytes(), b"png fixture")
        self.assertIn("shot%201.png", http.calls[0][1])
        client.screenshot(value, 0, lambda *args: None, lambda: False)
        self.assertEqual(len(http.calls), 1)

    def test_image_fit_keeps_pixel_art_integer_scaled_when_possible(self):
        self.assertEqual(core.fit_image(160, 144, 190, 150), (160, 144))
        self.assertEqual(core.fit_image(160, 144, 390, 295), (320, 288))
        self.assertEqual(core.fit_image(320, 288, 190, 150), (160, 144))
        self.assertEqual(core.fit_image(320, 288, 400, 300), (320, 288))
        self.assertEqual(core.fit_image(160, 144, 120, 80), (88, 80))


class BrowserTest(unittest.TestCase):
    def setUp(self):
        self.graphics = mock.Mock()
        self.graphics.size.return_value = (400, 300)
        for name, value in (("UP", 128), ("DOWN", 129), ("LEFT", 130), ("RIGHT", 131),
                            ("PAGE_UP", 132), ("PAGE_DOWN", 133), ("ESCAPE", 27)):
            setattr(self.graphics, "KEY_" + name, value)
        self.apps = mock.Mock()
        self.apps.find.return_value = {"name": "gameboy"}
        self.solaros = SimpleNamespace(gfx=self.graphics, apps=self.apps)
        spec = importlib.util.spec_from_file_location("gameboy_library_ui", APP / "gameboy_library.py")
        self.ui = importlib.util.module_from_spec(spec)
        with mock.patch.dict("sys.modules", {"solaros": self.solaros, "library": core}):
            spec.loader.exec_module(self.ui)
        self.library = mock.Mock()
        self.library.rom_path.return_value = "/Games/GameBoy/2048gb/game.gb"
        self.library.exists.return_value = True
        self.browser = self.ui.Browser(self.library)
        self.value = core.normalize_entry(entry())

    def test_successful_handoff_releases_graphics_and_propagates_system_exit(self):
        self.apps.launch.side_effect = SystemExit()
        with mock.patch.object(self.ui, "validate_rom"), self.assertRaises(SystemExit):
            self.browser.play(self.value)
        self.graphics.end.assert_called_once()
        self.graphics.begin.assert_not_called()
        self.assertFalse(self.browser.active)
        self.apps.launch.assert_called_once_with("gameboy", ["/Games/GameBoy/2048gb/game.gb"])

    def test_failed_launch_restores_browser_display_ownership(self):
        self.apps.launch.side_effect = OSError("admission failed")
        self.browser.message = mock.Mock()
        with mock.patch.object(self.ui, "validate_rom"):
            self.browser.play(self.value)
        self.graphics.end.assert_called_once()
        self.graphics.begin.assert_called_once()
        self.assertTrue(self.browser.active)
        self.browser.message.assert_called_once_with("Cannot launch Game Boy", "admission failed")

    def test_missing_image_service_does_not_block_game_details_or_fetch_images(self):
        self.browser.load_preview(self.value, 0)
        self.assertEqual(self.browser.preview_note, "Previews need media.image")
        self.library.screenshot.assert_not_called()

    def test_downloaded_game_offers_play_as_the_default_action(self):
        self.browser.load_preview = mock.Mock()
        self.browser.play = mock.Mock()
        self.browser.key = mock.Mock(side_effect=[13, 27])
        self.browser.details(self.value)
        self.browser.play.assert_called_once_with(self.value)

    def test_undownloaded_game_offers_download_and_play_without_an_option(self):
        self.library.exists.return_value = False
        self.browser.load_preview = mock.Mock()
        self.browser.play = mock.Mock()
        self.browser.key = mock.Mock(side_effect=[129, 13, 27])
        self.browser.details(self.value)
        self.browser.play.assert_called_once_with(self.value)

    def test_page_keys_navigate_catalog_pages(self):
        for key in (132, 133):
            self.browser.key = mock.Mock(side_effect=[key])
            self.assertEqual(self.browser.menu("Games", ["game"], catalog_pages=(2, 3)),
                             (0, key))

    def test_down_advances_catalog_immediately_after_the_last_visible_row(self):
        self.browser.key = mock.Mock(side_effect=[129] * 7)
        self.assertEqual(self.browser.menu("Games", [str(i) for i in range(7)],
                                           catalog_pages=(1, 3)), (6, 129))

    def test_catalog_page_size_matches_the_display_row_capacity(self):
        self.browser.message = mock.Mock()
        self.library.filtered_page.return_value = {"entries": [], "next_cursor": None}
        for size, rows in (((400, 300), 7), ((320, 240), 5), ((128, 64), 1), ((800, 480), 13)):
            with self.subTest(size=size):
                self.graphics.size.return_value = size
                browser = self.ui.Browser(self.library)
                browser.message = mock.Mock()
                browser.key = mock.Mock(side_effect=[27])
                browser.browse()
                self.library.filtered_page.assert_called_with("", "", [1, 0], False, page_size=rows)

    def test_catalog_arrows_do_not_wrap_at_the_first_or_last_page(self):
        for selected, keys in ((0, [128, 132, 13]), (1, [129, 133, 13])):
            self.browser.key = mock.Mock(side_effect=keys)
            self.assertEqual(self.browser.menu("Games", ["a", "b"], selected=selected,
                                               catalog_pages=(1, 1)), (selected, 13))

    def test_page_keys_scroll_downloads_and_other_local_lists(self):
        self.browser.key = mock.Mock(side_effect=[133, 133, 132, 13])
        self.assertEqual(self.browser.menu("Downloads", [str(i) for i in range(20)]),
                         (7, 13))

    def test_browse_keeps_filters_and_selects_previous_page_last_game_on_up(self):
        def page(number):
            return {"next_cursor": [number + 1, 1],
                    "entries": [entry(slug="game-%d-%d" % (number, i)) for i in range(7)]}

        self.browser.message = mock.Mock()
        self.browser.key = mock.Mock(side_effect=[129] * 7 + [128, 13, 27])
        self.browser.details = mock.Mock()
        # The final Enter opens page one's last game, followed by Escape.
        self.library.filtered_page.side_effect = [page(1), page(2), page(1), page(1)]
        self.browser.browse("search", "Puzzle")
        self.assertEqual(self.library.filtered_page.call_args_list,
                         [mock.call("search", "Puzzle", cursor, False, page_size=7)
                          for cursor in ([1, 0], [2, 1], [1, 0], [1, 0])])
        self.assertEqual(self.browser.details.call_args[0][0]["slug"], "game-1-6")

    def test_browse_stops_on_partial_final_page_and_uses_the_filtered_total(self):
        self.library.filtered_page.side_effect = [
            {"entries": [entry(slug="game-%d" % i) for i in range(7)], "next_cursor": [2, 1]},
            {"entries": [entry(), entry(slug="last")], "next_cursor": None},
        ]
        self.browser.message = mock.Mock()
        self.browser.frame = mock.Mock()
        self.browser.key = mock.Mock(side_effect=[133, 133, 27])
        self.browser.browse(category="Puzzle")
        self.assertEqual(self.library.filtered_page.call_args_list,
                         [mock.call("", "Puzzle", cursor, False, page_size=7)
                          for cursor in ([1, 0], [2, 1])])
        self.assertTrue(any(call.args[1] == "9 games / page 2 of 2"
                            for call in self.browser.frame.call_args_list))


if __name__ == "__main__":
    unittest.main()
