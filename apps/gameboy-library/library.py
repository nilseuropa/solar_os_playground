"""Bounded Homebrew Hub client and persistent Game Boy downloads."""

import gc
import json

API = "https://hh3.gbdev.io/api"
ASSETS = "https://raw.githubusercontent.com/gbdev/database/master/entries/"
PAGE_SIZE = 8
MAX_PAGE_SIZE = 24
JSON_LIMIT = 64 * 1024
ROM_LIMIT = 4 * 1024 * 1024
IMAGE_LIMIT = 512 * 1024
CATEGORIES = (
    "Action", "Adventure", "Arcade", "Card Game", "Educational", "Fighting",
    "Interactive Fiction", "Platform", "Puzzle", "Racing", "Rhythm", "RPG",
    "Shooter", "Simulation", "Sports", "Strategy", "Survival", "Visual Novel",
)
# Matches the cartridge table in SolarOS's vendored Peanut-GB core.
CARTRIDGES = (0, 1, 2, 3, 5, 6, 8, 9, 11, 12, 13, 15, 16, 17, 18, 19,
              25, 26, 27, 28, 29, 30)


class Cancelled(Exception):
    pass


def text(value, limit=200):
    if not isinstance(value, str):
        return ""
    return " ".join("".join(c if ord(c) >= 32 and ord(c) != 127 else " "
                            for c in value).split())[:limit]


def quote(value):
    result = []
    for byte in value.encode("utf-8"):
        if (65 <= byte <= 90 or 97 <= byte <= 122 or 48 <= byte <= 57 or
                byte in (45, 46, 95, 126)):
            result.append(chr(byte))
        else:
            result.append("%%%02X" % byte)
    return "".join(result)


def safe_slug(value):
    return (isinstance(value, str) and 0 < len(value) <= 128 and
            value not in (".", "..") and
            all(c in "abcdefghijklmnopqrstuvwxyz0123456789._-" for c in value))


def asset_url(slug, filename):
    if not safe_slug(slug) or not isinstance(filename, str) or len(filename) > 512:
        raise ValueError("Invalid catalog asset path")
    parts = filename.split("/")
    if any(not p or p in (".", "..") or "\\" in p or ":" in p or
           any(ord(c) < 32 or ord(c) == 127 for c in p) for p in parts):
        raise ValueError("Invalid catalog asset path")
    return ASSETS + quote(slug) + "/" + "/".join(quote(p) for p in parts)


def author(value):
    if isinstance(value, list):
        return ", ".join(author(part) for part in value[:8])[:160]
    if isinstance(value, dict):
        value = value.get("name", value.get("username", ""))
    return text(value, 160)


def normalize_entry(entry):
    if not isinstance(entry, dict) or not safe_slug(entry.get("slug")):
        return None
    if entry.get("platform", "GB") != "GB" or entry.get("typetag", "game") != "game":
        return None
    slug = entry["slug"]
    files = []
    source_files = entry.get("files", [])
    for item in source_files[:24] if isinstance(source_files, list) else []:
        if not isinstance(item, dict):
            continue
        filename = item.get("filename", "")
        if not isinstance(filename, str) or not filename.lower().endswith(".gb"):
            continue
        if item.get("playable") is False:
            continue
        try:
            asset_url(slug, filename)
        except ValueError:
            continue
        files.append({"filename": filename, "default": item.get("default") is True})
    if not files:
        return None
    files.sort(key=lambda item: not item["default"])
    shots = []
    source_shots = entry.get("screenshots", [])
    for name in source_shots[:8] if isinstance(source_shots, list) else []:
        if not isinstance(name, str) or not name.lower().endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp")):
            continue
        try:
            asset_url(slug, name)
            shots.append(name)
        except ValueError:
            pass
    license_value = entry.get("gameLicense", entry.get("license", "Not specified"))
    if isinstance(license_value, dict):
        license_value = license_value.get("name", license_value.get("spdx", "See source"))
    tags = entry.get("tags", [])
    return {
        "slug": slug, "title": text(entry.get("title"), 120) or slug,
        "developer": author(entry.get("developer", "")),
        "description": text(entry.get("description", ""), 2048),
        "license": text(license_value, 120) or "See source",
        "tags": [text(t, 40) for t in tags[:24] if isinstance(t, str)]
                if isinstance(tags, list) else [],
        "screenshots": shots, "files": files, "platform": "GB", "typetag": "game",
    }


def search_url(title="", category="", page=1, page_size=PAGE_SIZE):
    page_size = max(1, min(MAX_PAGE_SIZE, page_size))
    url = (API + "/search?platform=GB&typetag=game&results=" + str(page_size) +
           "&page=" + str(max(1, page)) + "&sort=title&order=asc")
    if title:
        # q uses case-insensitive title/slug search in the current API.
        url += "&q=" + quote(title)
    if category:
        url += "&tags=" + quote(category)
    return url


def validate_rom(path):
    with open(path, "rb") as source:
        header = source.read(0x150)
        source.seek(0, 2)
        size = source.tell()
    if len(header) != 0x150 or size > ROM_LIMIT:
        raise ValueError("ROM must contain a Game Boy header and be at most 4 MiB")
    if header[0x143] == 0xC0:
        raise ValueError("Game Boy Color-only ROM; the installed emulator supports DMG")
    if header[0x148] > 7 or size < (32768 << header[0x148]):
        raise ValueError("ROM is truncated or has an unsupported size")
    if header[0x149] > 5:
        raise ValueError("Unsupported cartridge RAM size")
    checksum = 0
    for byte in header[0x134:0x14D]:
        checksum = (checksum - byte - 1) & 255
    if checksum != header[0x14D]:
        raise ValueError("Invalid Game Boy header checksum")
    if header[0x147] not in CARTRIDGES:
        raise ValueError("Cartridge controller is unsupported by the Game Boy emulator")
    return size


def fit_image(width, height, box_width, box_height):
    if min(width, height, box_width, box_height) < 1:
        return 0, 0
    # Many catalog captures are already enlarged 2x or 3x. Fit those to an
    # integer multiple of the original 160x144 grid as well.
    if width % 160 == 0 and height % 144 == 0 and width // 160 == height // 144:
        native_scale = min(box_width // 160, box_height // 144)
        if native_scale >= 1:
            return 160 * native_scale, 144 * native_scale
    scale = min(box_width // width, box_height // height)
    if scale >= 1:
        return width * scale, height * scale
    if box_width * height <= box_height * width:
        return box_width, max(1, height * box_width // width)
    return max(1, width * box_height // height), box_height


class Library:
    def __init__(self, http, storage, root, should_exit, clock):
        self.http = http
        self.storage = storage
        self.root = root.rstrip("/")
        self.should_exit = should_exit
        self.clock = clock
        storage.makedirs(self.root + "/cache")

    def exists(self, path):
        return self.storage.exists(path)

    def remove(self, path):
        if self.exists(path):
            self.storage.remove(path)

    def replace(self, temporary, path):
        # FAT and other SolarOS mounts need not support rename over a file.
        backup = path + ".old"
        self.remove(backup)
        had_old = self.exists(path)
        if had_old:
            self.storage.rename(path, backup)
        try:
            self.storage.rename(temporary, path)
        except Exception:
            if had_old:
                self.storage.rename(backup, path)
            raise
        self.remove(backup)

    def read_json(self, path):
        with open(path, "rb") as source:
            data = source.read(JSON_LIMIT + 1)
        if len(data) > JSON_LIMIT:
            raise ValueError("Cached metadata is too large")
        return json.loads(data)

    def write_json(self, path, value):
        temporary = path + ".part"
        try:
            with open(temporary, "w") as output:
                json.dump(value, output)
            self.replace(temporary, path)
        finally:
            self.remove(temporary)

    def directory(self, entry):
        if not safe_slug(entry.get("slug")):
            raise ValueError("Invalid game identifier")
        return self.root + "/" + entry["slug"]

    def rom_path(self, entry):
        return self.directory(entry) + "/game.gb"

    def remember(self, entry):
        directory = self.directory(entry)
        self.storage.makedirs(directory)
        self.write_json(directory + "/entry.json", entry)

    def cache_path(self, kind, key):
        slot = 0
        for byte in key.encode():
            slot = (slot * 33 + byte) % 65521
        return self.root + "/cache/%s-%d.json" % (kind, slot % 8)

    def page(self, title="", category="", page=1, refresh=False, page_size=PAGE_SIZE):
        page_size = max(1, min(MAX_PAGE_SIZE, page_size))
        url = search_url(title, category, page, page_size)
        # Eight bounded disk slots; retain the URL to detect hash collisions.
        path = self.cache_path("page", url)
        cached = None
        try:
            saved = self.read_json(path)
            if saved.get("url") == url:
                cached = saved["page"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if cached is not None and not refresh:
            cached["cached"] = True
            return cached
        try:
            response = self.http.get(url, {"Accept": "application/json",
                                          "User-Agent": "SolarOS-GameBoy-Library/1.0"},
                                     15000, JSON_LIMIT, True)
            if response.get("status_code") != 200:
                raise OSError("Catalog HTTP %s" % response.get("status_code"))
            if response.get("truncated"):
                raise ValueError("Catalog page is too large")
            document = json.loads(response["body"])
            if not isinstance(document, dict) or not isinstance(document.get("entries"), list):
                raise ValueError("Invalid catalog response")
            entries = []
            for item in document["entries"][:page_size]:
                entry = normalize_entry(item)
                if entry is not None:
                    entries.append(entry)
            result = {"entries": entries, "page": max(1, int(document["page_current"])),
                      "pages": max(1, int(document["page_total"])),
                      "total": max(0, int(document["results"]))}
            self.write_json(path, {"url": url, "page": result})
            return result
        except Exception:
            if self.should_exit():
                raise Cancelled()
            if cached is None:
                raise
            cached["cached"] = True
            cached["offline"] = True
            return cached
        finally:
            gc.collect()

    def filtered_page(self, title="", category="", cursor=None, refresh=False,
                      page_size=PAGE_SIZE):
        """Fill a screen with supported entries, carrying the exact next position."""
        page_size = max(1, min(MAX_PAGE_SIZE, page_size))
        source_page, offset = cursor if cursor is not None else (1, 0)
        if source_page < 1 or not 0 <= offset <= page_size:
            raise ValueError("Invalid catalog position")
        query = search_url(title, category, 1, page_size)
        key = "filtered-v1:" + search_url(title, category, source_page, page_size) + ":%d" % offset
        path = self.cache_path("view", key)
        cached = None
        try:
            saved = self.read_json(path)
            if saved.get("url") == key:
                cached = saved["view"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if cached is not None and not refresh:
            cached["cached"] = True
            return cached
        if refresh:
            # Rebuild the filter's positions after upstream edits. Keep the
            # current cached view available for an offline refresh fallback.
            for slot in range(8):
                self.remove(self.root + "/cache/page-%d.json" % slot)
                view_path = self.root + "/cache/view-%d.json" % slot
                try:
                    if self.read_json(view_path).get("query") == query:
                        self.remove(view_path)
                except (OSError, ValueError, TypeError):
                    pass
        try:
            entries, batch, next_cursor = [], None, None
            all_cached, offline = True, False
            while True:
                if self.should_exit():
                    raise Cancelled()
                if batch is None:
                    batch = self.page(title, category, source_page, page_size=page_size)
                    if batch["page"] != source_page:
                        raise ValueError("Catalog changed; refresh the search")
                    all_cached = all_cached and bool(batch.get("cached"))
                    offline = offline or bool(batch.get("offline"))
                if offset < len(batch["entries"]):
                    if len(entries) == page_size:
                        # Look ahead to one supported game so a full final
                        # page never advertises an empty following page.
                        next_cursor = [source_page, offset]
                        break
                    entries.append(batch["entries"][offset])
                    offset += 1
                elif source_page >= batch["pages"]:
                    break
                else:
                    source_page += 1
                    offset, batch = 0, None
            result = {"entries": entries, "next_cursor": next_cursor}
            if all_cached:
                result["cached"] = True
            if offline:
                result["offline"] = True
            self.write_json(path, {"url": key, "query": query, "view": result})
            return result
        except Cancelled:
            raise
        except Exception:
            if self.should_exit():
                raise Cancelled()
            if cached is None:
                raise
            cached["cached"] = True
            cached["offline"] = True
            self.write_json(path, {"url": key, "query": query, "view": cached})
            return cached
        finally:
            gc.collect()

    def transfer(self, url, path, limit, progress, cancelled):
        handle = self.http.stream_open("GET", url, None,
                                       {"User-Agent": "SolarOS-GameBoy-Library/1.0"},
                                       15000, True)
        received, total, status = 0, -1, 0
        complete = False
        started = self.clock()
        try:
            with open(path, "wb") as output:
                while True:
                    if self.should_exit() or cancelled():
                        raise Cancelled()
                    if self.clock() - started > 120000:
                        raise OSError("Download timed out")
                    event = self.http.stream_read(handle, 100)
                    if event is None:
                        continue
                    kind = event.get("type")
                    if kind == "response":
                        status = event.get("status_code", 0)
                        total = event.get("content_length", -1)
                        if status != 200:
                            raise OSError("Download HTTP %s" % status)
                        if total > limit:
                            raise ValueError("Download exceeds size limit")
                    elif kind == "data":
                        data = event.get("data", b"")
                        received += len(data)
                        if received > limit:
                            raise ValueError("Download exceeds size limit")
                        if output.write(data) != len(data):
                            raise OSError("Storage write was incomplete")
                        progress(received, total)
                    elif kind == "error":
                        raise OSError(event.get("error_name", "Download failed"))
                    elif kind == "complete":
                        complete = True
                        break
                output.flush()
            if not complete or status != 200 or received < 1 or (total >= 0 and received != total):
                raise OSError("Download was incomplete")
            return received
        except BaseException:
            self.remove(path)
            raise
        finally:
            self.http.stream_close(handle)
            gc.collect()

    def download(self, entry, file_index, progress, cancelled):
        self.remember(entry)
        path = self.rom_path(entry)
        if self.exists(path):
            validate_rom(path)
            return path
        temporary = path + ".part"
        try:
            self.transfer(asset_url(entry["slug"], entry["files"][file_index]["filename"]),
                          temporary, ROM_LIMIT, progress, cancelled)
            validate_rom(temporary)
            self.replace(temporary, path)
            return path
        finally:
            self.remove(temporary)

    def screenshot(self, entry, index, progress, cancelled):
        self.remember(entry)
        name = entry["screenshots"][index]
        path = self.directory(entry) + "/preview-%d.%s" % (index, name.rsplit(".", 1)[-1].lower())
        if self.exists(path):
            return path
        temporary = path + ".part"
        try:
            self.transfer(asset_url(entry["slug"], name), temporary, IMAGE_LIMIT, progress, cancelled)
            self.replace(temporary, path)
            return path
        finally:
            self.remove(temporary)

    def downloads(self):
        entries, cursor = [], None
        while True:
            if self.should_exit():
                raise Cancelled()
            listing = self.storage.scandir(self.root, cursor, 32)
            for item in listing["entries"]:
                slug = item.get("name", "")
                if not item.get("is_dir") or not safe_slug(slug) or slug == "cache":
                    continue
                if not self.exists(self.root + "/" + slug + "/game.gb"):
                    continue
                try:
                    entry = normalize_entry(self.read_json(self.root + "/" + slug + "/entry.json"))
                    if entry and entry["slug"] == slug:
                        entries.append({"slug": slug, "title": entry["title"],
                                        "developer": entry["developer"]})
                except (OSError, ValueError, TypeError):
                    pass
            cursor = listing.get("next_cursor")
            if cursor is None or len(entries) >= 256:
                break
        entries.sort(key=lambda entry: entry["title"].lower())
        return entries[:256]

    def local_entry(self, slug):
        if not safe_slug(slug):
            raise ValueError("Invalid game identifier")
        entry = normalize_entry(self.read_json(self.root + "/" + slug + "/entry.json"))
        if not entry or entry["slug"] != slug:
            raise ValueError("Invalid saved game metadata")
        return entry
