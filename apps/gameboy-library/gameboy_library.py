"""Game Boy homebrew browser, screenshot viewer, downloader, and launcher."""

import gc
import sys

import solaros
from solaros import gfx

from library import CATEGORIES, MAX_PAGE_SIZE, Cancelled, Library, fit_image, text, validate_rom


ROOT = "/Games/GameBoy"
ENTER = (10, 13)
BACKSPACE = (8, 127)


def clipped(value, width):
    value = text(value, 2048)
    columns = max(0, width // 6)
    return value if len(value) <= columns else value[:max(0, columns - 1)] + "~"


def wrapped(value, columns):
    columns = max(1, columns)
    lines = []
    remaining = text(value, 2048)
    while remaining:
        if len(remaining) <= columns:
            lines.append(remaining)
            break
        end = remaining.rfind(" ", 0, columns + 1)
        if end < 1:
            end = columns
        lines.append(remaining[:end])
        remaining = remaining[end:].lstrip()
    return lines


class Browser:
    def __init__(self, library):
        self.library = library
        self.width, self.height = gfx.size()
        self.compact = self.height < 160
        self.top = 30 if self.compact else 49
        self.bottom = self.height - (14 if self.compact else 28)
        self.image_api = getattr(solaros, "image", None)
        if self.image_api and not all(hasattr(self.image_api, method)
                                      for method in ("open", "size", "draw", "close")):
            self.image_api = None
        self.preview = None
        self.preview_note = ""
        self.last_progress = -1000
        self.active = True

    def label(self, x, y, value, width=None, bold=False):
        gfx.font(gfx.FONT_BOLD if bold else gfx.FONT_SMALL)
        # Bold glyphs can exceed the small font's six-pixel advance.
        width = self.width - x - 6 if width is None else width
        if bold:
            value = text(value, max(0, width // 8))
        gfx.text(x, y, clipped(value, width))

    def frame(self, title, subtitle="", footer="Enter select   Esc back"):
        gfx.clear(gfx.WHITE)
        gfx.color(gfx.BLACK)
        self.label(8, 13 if self.compact else 21, title,
                   self.width - 16, bold=not self.compact)
        if self.height >= 90:
            self.label(8, 26 if self.compact else 37, subtitle)
        gfx.line(6, self.top - 5, self.width - 7, self.top - 5)
        gfx.line(6, self.bottom + 1, self.width - 7, self.bottom + 1)
        self.label(8, self.height - 3 if self.compact else self.height - 10,
                   footer, self.width - 16)

    def key(self, timeout=200):
        if solaros.should_exit():
            raise Cancelled()
        return gfx.getch(timeout)

    def back(self, key):
        return key == gfx.KEY_ESCAPE or key in (ord("q"), ord("Q"))

    def cancel_transfer(self):
        key = gfx.getch(0)
        return self.back(key)

    def message(self, title, message, wait=True):
        self.frame(title, "Game Boy Library", "Press any key" if wait else "Esc cancel")
        for index, line in enumerate(wrapped(message, (self.width - 20) // 6)):
            y = self.top + 11 + index * 12
            if y >= self.bottom:
                break
            self.label(10, y, line)
        gfx.present()
        if wait:
            while self.key() is None:
                pass

    def progress(self, title):
        self.last_progress = -1000

        def update(received, total):
            now = solaros.time.uptime_ms()
            if now - self.last_progress < 400:
                return
            self.last_progress = now
            if total > 0:
                body = "%d%%   %d / %d KiB" % (min(100, received * 100 // total),
                                                received // 1024, (total + 1023) // 1024)
            else:
                body = "%d KiB received" % (received // 1024)
            self.message(title, body, False)
        self.message(title, "Connecting...", False)
        return update

    def menu_layout(self):
        row_height = 16 if self.compact else 30
        visible = max(1, (self.bottom - self.top - 3) // row_height)
        return row_height, visible

    def menu(self, title, items, subtitle="", selected=0, footer="Enter select   Esc back",
             catalog_pages=None):
        row_height, visible = self.menu_layout()
        while True:
            self.frame(title, subtitle, footer)
            start = selected // visible * visible
            for offset, item in enumerate(items[start:start + visible]):
                y = self.top + offset * row_height
                chosen = start + offset == selected
                if chosen:
                    gfx.color(gfx.BLACK)
                    gfx.fill_rect(6, y, self.width - 12, row_height - 2)
                    gfx.color(gfx.WHITE)
                else:
                    gfx.color(gfx.BLACK)
                label = item.get("title", "") if isinstance(item, dict) else item
                self.label(12, y + 11 if self.compact else y + 13, label)
                if isinstance(item, dict) and not self.compact:
                    self.label(12, y + 25, item.get("developer", item.get("subtitle", "")))
            gfx.color(gfx.BLACK)
            if not items:
                self.label(10, self.top + 12, "No games found")
            gfx.present()
            while True:
                key = self.key()
                if key is not None:
                    break
            if self.back(key):
                return None, key
            if key in ENTER and items:
                return selected, key
            if key in (gfx.KEY_UP, gfx.KEY_DOWN) and (items or catalog_pages):
                step = -1 if key == gfx.KEY_UP else 1
                target = selected + step
                if catalog_pages is None:
                    selected = target % len(items)
                elif target < 0:
                    if catalog_pages[0] > 1:
                        return selected, key
                elif target >= len(items):
                    if catalog_pages[0] < catalog_pages[1]:
                        return selected, key
                else:
                    selected = target
            elif key in (gfx.KEY_PAGE_UP, gfx.KEY_PAGE_DOWN):
                if catalog_pages is not None:
                    if ((key == gfx.KEY_PAGE_UP and catalog_pages[0] > 1) or
                            (key == gfx.KEY_PAGE_DOWN and catalog_pages[0] < catalog_pages[1])):
                        return selected, key
                elif items:
                    step = -visible if key == gfx.KEY_PAGE_UP else visible
                    selected = max(0, min(len(items) - 1, selected + step))
            elif key in (gfx.KEY_LEFT, gfx.KEY_RIGHT, ord("r"), ord("R"), ord("/")):
                return selected, key

    def search_text(self, initial=""):
        value = initial
        while True:
            self.frame("Search games", "Title or part of a title", "Enter search   Esc cancel")
            y = self.top + 15
            self.label(10, y, value[-max(1, (self.width - 26) // 6):] + "_")
            if self.bottom - y > 35:
                self.label(10, y + 30, "Original Game Boy homebrew")
            gfx.present()
            while True:
                key = self.key()
                if key is not None:
                    break
            if key == gfx.KEY_ESCAPE:
                return None
            if key in ENTER:
                return value.strip()
            if key in BACKSPACE or key == getattr(gfx, "KEY_DELETE", -1):
                value = value[:-1]
            elif isinstance(key, int) and 32 <= key <= 126 and len(value) < 80:
                value += chr(key)

    def browse(self, title="", category=""):
        page, selected, refresh = 1, 0, False
        page_size = min(MAX_PAGE_SIZE, self.menu_layout()[1])
        cursors = [[1, 0]]
        known_pages, known_total = None, None
        while True:
            self.message("Homebrew Hub", "Loading games...", False)
            try:
                result = self.library.filtered_page(title, category, cursors[page - 1],
                                                    refresh, page_size=page_size)
            except Cancelled:
                return
            except Exception as error:
                self.message("Catalog unavailable", str(error) + ". Downloaded games are available offline.")
                return
            refresh = False
            heading = ("Search: " + title) if title else (category or "All games")
            entries = result["entries"]
            next_cursor = result["next_cursor"]
            if next_cursor is None:
                known_pages, known_total = page, (page - 1) * page_size + len(entries)
            if known_pages is not None:
                note = "%d games / page %d of %d" % (known_total, page, known_pages)
            else:
                note = "%d shown / page %d / more" % (len(entries), page)
            if result.get("offline"):
                note += " / offline cache"
            elif result.get("cached"):
                note += " / cached"
            selected = (max(0, len(entries) - 1) if selected < 0 else
                        min(selected, max(0, len(entries) - 1)))
            selected, key = self.menu(heading, entries, note, selected,
                                      "Enter details  PgUp/PgDn page  r refresh" if not self.compact
                                      else "Enter  PgUp/Dn page",
                                      catalog_pages=(page, page + 1 if next_cursor is not None else page))
            if selected is None:
                return
            if key in (gfx.KEY_LEFT, gfx.KEY_PAGE_UP, gfx.KEY_UP) and page > 1:
                page -= 1
                selected = -1 if key == gfx.KEY_UP else 0
            elif key in (gfx.KEY_RIGHT, gfx.KEY_PAGE_DOWN, gfx.KEY_DOWN) and next_cursor is not None:
                if page >= 1024:
                    self.message("Narrow the search", "Use a title or category to browse more results.")
                    continue
                if page < len(cursors):
                    if cursors[page] != next_cursor:
                        cursors = cursors[:page] + [next_cursor]
                        known_pages, known_total = None, None
                else:
                    cursors.append(next_cursor)
                page += 1
                selected = 0
            elif key in (ord("r"), ord("R")):
                page, selected = 1, 0
                cursors = [[1, 0]]
                known_pages, known_total = None, None
                refresh = True
            elif key == ord("/"):
                query = self.search_text(title)
                if query is not None:
                    title, page, selected = query, 1, 0
                    cursors = [[1, 0]]
                    known_pages, known_total = None, None
            elif key in ENTER:
                self.details(entries[selected])

    def close_preview(self):
        if self.preview is not None:
            self.image_api.close(self.preview)
            self.preview = None

    def load_preview(self, entry, index):
        self.close_preview()
        self.preview_note = "No screenshots available"
        if not entry["screenshots"]:
            return
        if self.image_api is None:
            self.preview_note = "Previews need media.image"
            return
        path = None
        try:
            path = self.library.screenshot(entry, index, self.progress("Loading screenshot"),
                                           self.cancel_transfer)
            self.preview = self.image_api.open(path)
            self.preview_note = ""
        except Cancelled:
            self.preview_note = "Preview cancelled; press s to retry"
        except Exception:
            self.preview_note = "Preview unavailable; press s to retry"
            if path is not None:
                # Do not keep a truncated or undecodable image as a cache hit.
                self.library.remove(path)

    def draw_preview(self, x, y, width, height):
        if self.preview is not None:
            source_w, source_h = self.image_api.size(self.preview)
            draw_w, draw_h = fit_image(source_w, source_h, width, height)
            if draw_w and draw_h:
                self.image_api.draw(self.preview, x + (width - draw_w) // 2,
                                    y + (height - draw_h) // 2, draw_w, draw_h)
        else:
            for index, line in enumerate(wrapped(self.preview_note, max(1, width // 6))):
                if (index + 1) * 12 > height:
                    break
                self.label(x, y + 12 * (index + 1), line, width)

    def fullscreen(self, entry, index):
        while True:
            gfx.clear(gfx.WHITE)
            gfx.color(gfx.BLACK)
            self.draw_preview(0, 0, self.width, self.height)
            gfx.present()
            key = None
            while key is None:
                key = self.key()
            if self.back(key) or key in ENTER or key in (ord("s"), ord("S")):
                return index
            if key in (gfx.KEY_LEFT, gfx.KEY_RIGHT) and entry["screenshots"]:
                index = (index + (-1 if key == gfx.KEY_LEFT else 1)) % len(entry["screenshots"])
                self.load_preview(entry, index)

    def download(self, entry):
        if self.library.exists(self.library.rom_path(entry)):
            return self.library.rom_path(entry)
        file_index = 0
        if len(entry["files"]) > 1:
            file_index, key = self.menu("Choose ROM", [f["filename"] for f in entry["files"]],
                                        entry["title"])
            if file_index is None or key not in ENTER:
                return None
        try:
            path = self.library.download(entry, file_index, self.progress("Downloading ROM"),
                                         self.cancel_transfer)
            self.message("Download complete", entry["title"] + "\nSaved to " + path)
            return path
        except Cancelled:
            return None
        except Exception as error:
            self.message("Download failed", str(error))
            return None

    def play(self, entry):
        apps = getattr(solaros, "apps", None)
        if apps is None or not hasattr(apps, "launch") or apps.find("gameboy") is None:
            self.message("Game Boy emulator needed", "Install the native module with: pkg install gameboy")
            return
        path = self.library.rom_path(entry)
        if not self.library.exists(path):
            path = self.download(entry)
            if path is None:
                return
        try:
            validate_rom(path)
        except Exception as error:
            self.message("Cannot play this ROM", str(error))
            return
        self.close_preview()
        gfx.end()
        self.active = False
        try:
            apps.launch("gameboy", [path])
        except Exception as error:
            # A successful handoff raises SystemExit, which must pass through.
            gfx.begin()
            self.active = True
            self.message("Cannot launch Game Boy", str(error))

    def details(self, entry):
        shot, selected = 0, 0
        self.load_preview(entry, shot)
        try:
            while True:
                installed = self.library.exists(self.library.rom_path(entry))
                self.frame(entry["title"], entry["developer"],
                           "d download  l play  s screenshot  Esc back" if not self.compact else
                           "d save l play s shot")
                wide = self.width >= 320 and self.height >= 220
                text_width = self.width // 2 - 18 if wide else self.width - 20
                x, y = 10, self.top + 10
                lines = ["ORIGINAL GAME BOY", " / ".join(entry["tags"][:3]),
                         "License: " + entry["license"],
                         "Downloaded" if installed else "Ready to download"]
                if entry["description"]:
                    lines.append(entry["description"])
                line_limit = max(0, (self.bottom - self.top - (85 if wide else 54)) // 12)
                line_index = 0
                for paragraph in lines:
                    for line in wrapped(paragraph, text_width // 6):
                        if line_index >= line_limit:
                            break
                        self.label(x, y + line_index * 12, line, text_width)
                        line_index += 1
                if wide:
                    box_x = self.width // 2 + 3
                    box_h = min(150, self.bottom - self.top - 40)
                    self.draw_preview(box_x, self.top, self.width - box_x - 10, box_h)
                    if entry["screenshots"]:
                        self.label(box_x, self.top + box_h + 13,
                                   "%d/%d  Left/Right" % (shot + 1, len(entry["screenshots"])))
                actions = ([("Play", "play"), ("ROM location", "location"),
                            ("Screenshot", "screenshot")] if installed else
                           [("Download ROM", "download"), ("Download and play", "play"),
                            ("Screenshot", "screenshot")])
                action_h = 18 if self.compact else 20
                action_y = self.bottom - action_h * (1 if self.compact else len(actions)) - 2
                for index, action in enumerate(actions):
                    if self.compact and index != selected:
                        continue
                    row_y = action_y + (0 if self.compact else index * action_h)
                    gfx.color(gfx.BLACK)
                    if index == selected:
                        gfx.fill_rect(6, row_y, (text_width + 10 if wide else self.width - 12), action_h - 2)
                        gfx.color(gfx.WHITE)
                    self.label(12, row_y + 12, action[0], text_width)
                gfx.color(gfx.BLACK)
                gfx.present()
                key = None
                while key is None:
                    key = self.key()
                if self.back(key):
                    return
                action = None
                if key == gfx.KEY_UP:
                    selected = (selected - 1) % len(actions)
                elif key == gfx.KEY_DOWN:
                    selected = (selected + 1) % len(actions)
                elif key in (gfx.KEY_LEFT, gfx.KEY_RIGHT) and entry["screenshots"]:
                    shot = (shot + (-1 if key == gfx.KEY_LEFT else 1)) % len(entry["screenshots"])
                    self.load_preview(entry, shot)
                elif key in (ord("d"), ord("D")):
                    action = "download"
                elif key in (ord("l"), ord("L")):
                    action = "play"
                elif key in (ord("s"), ord("S")):
                    action = "screenshot"
                elif key in ENTER:
                    action = actions[selected][1]
                if action == "download":
                    self.download(entry)
                elif action == "play":
                    self.play(entry)
                elif action == "location":
                    self.message("Saved ROM", self.library.rom_path(entry) +
                                 ". Open game.gb in Files to play.")
                elif action == "screenshot":
                    if self.preview is None:
                        self.load_preview(entry, shot)
                    shot = self.fullscreen(entry, shot)
        finally:
            self.close_preview()

    def downloads(self):
        selected = 0
        while True:
            entries = self.library.downloads()
            selected, key = self.menu("Downloaded games", entries, "Ready to play offline", selected,
                                      "Enter details   Esc back")
            if selected is None:
                return
            if key in ENTER:
                self.details(self.library.local_entry(entries[selected]["slug"]))

    def run(self, search=None, category=None, downloads=False):
        if search is not None or category is not None:
            self.browse(search or "", category or "")
        elif downloads:
            self.downloads()
        selected = 0
        while True:
            selected, key = self.menu("Game Boy Library", [
                {"title": "Search games", "subtitle": "Find a game by name"},
                {"title": "Browse categories", "subtitle": "Puzzle, platform, RPG, and more"},
                {"title": "All games", "subtitle": "Browse the Homebrew Hub catalog"},
                {"title": "Downloaded games", "subtitle": "Your offline library"},
            ], "HOMEBREW HUB / ORIGINAL GAME BOY", selected, "Enter select   q quit")
            if selected is None:
                return
            if key == ord("/"):
                selected, key = 0, 13
            if key not in ENTER:
                continue
            if selected == 0:
                title = self.search_text()
                if title is not None:
                    self.browse(title)
            elif selected == 1:
                category_index, category_key = self.menu("Browse categories", CATEGORIES,
                                                          "Homebrew Hub genres")
                if category_index is not None and category_key in ENTER:
                    self.browse(category=CATEGORIES[category_index])
            elif selected == 2:
                self.browse()
            else:
                self.downloads()


def main():
    args = sys.argv[1:]
    search, category = None, None
    for flag in ("--search", "--category"):
        if flag in args:
            index = args.index(flag)
            if index + 1 >= len(args):
                raise ValueError(flag + " needs a value")
            if flag == "--search":
                search = args[index + 1]
            else:
                category = args[index + 1]
    gfx.begin()
    browser = None
    try:
        http = getattr(solaros, "http", None)
        if http is None or not hasattr(http, "stream_open"):
            raise RuntimeError("Game Boy Library needs the SolarOS HTTP client")
        root = solaros.storage.resolve(ROOT)
        library = Library(http, solaros.storage, root, solaros.should_exit,
                          solaros.time.uptime_ms)
        browser = Browser(library)
        browser.run(search, category, "--downloads" in args)
    except Cancelled:
        pass
    finally:
        if browser is not None:
            browser.close_preview()
        if browser is None or browser.active:
            gfx.end()
        gc.collect()


if __name__ == "__main__":
    main()
