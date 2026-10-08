import json
import sys

import solaros
from solaros import tui

APP = "sollama"
DEFAULT_HOST = ""
MAX_BYTES = 48 * 1024
TIMEOUT_MS = 180000
TITLE_LEN = 42
KEEP_MESSAGES = 6

KEY_ENTER = 10
KEY_RETURN = 13
KEY_BACKSPACE = 8
KEY_DELETE_CHAR = 127


def clip(text, width):
    text = "" if text is None else str(text)
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return text[:1]
    return text[: width - 1] + ">"


def pad(text, width):
    text = clip(text, width)
    if len(text) >= width:
        return text
    return text + (" " * (width - len(text)))


def wrap_indexed(text, width):
    lines = []
    index = 0
    length = len(text)
    if width < 1:
        width = 1
    if length == 0:
        return [("", 0)]
    while index < length:
        if text[index] == "\n":
            lines.append(("", index))
            index += 1
            continue
        end = min(length, index + width)
        newline = text.find("\n", index, end)
        if newline != -1:
            lines.append((text[index:newline], index))
            index = newline + 1
            continue
        if end < length and text[end] not in " \n":
            cut = text.rfind(" ", index, end)
            if cut > index:
                lines.append((text[index:cut], index))
                index = cut + 1
                continue
        lines.append((text[index:end], index))
        index = end
    return lines


def wrap(text, width):
    text = "" if text is None else str(text).replace("\r", "")
    if width <= 1:
        return [text]
    lines = []
    for block in text.split("\n"):
        if block == "":
            lines.append("")
            continue
        while len(block) > width:
            cut = block.rfind(" ", 0, width)
            if cut < 1:
                cut = width
            lines.append(block[:cut])
            block = block[cut:].lstrip() if cut < width else block[cut:]
        lines.append(block)
    return lines or [""]


def word_left(text, cursor):
    index = cursor
    while index > 0 and text[index - 1] == " ":
        index -= 1
    while index > 0 and text[index - 1] != " ":
        index -= 1
    return index


def word_right(text, cursor):
    index = cursor
    length = len(text)
    while index < length and text[index] == " ":
        index += 1
    while index < length and text[index] != " ":
        index += 1
    return index


def safe_name(name, fallback):
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    safe = "".join(ch for ch in str(name) if ch in allowed)
    return safe or fallback


def decode_body(response):
    body = response.get("body", b"")
    if isinstance(body, bytes):
        return body.decode("utf-8")
    return str(body)


class Store:
    def __init__(self, name):
        self.name = safe_name(name, APP)
        self.root = self._root()
        self._mkdir(self.root)
        self._mkdir(self.root + "/chats")

    def _root(self):
        if solaros.storage.exists("/sdcard"):
            return "/sdcard/" + self.name
        return "/" + self.name

    def _mkdir(self, path):
        try:
            solaros.storage.makedirs(path, True)
        except TypeError:
            solaros.storage.makedirs(path)
        except OSError:
            pass

    def config_path(self):
        return self.root + "/config.json"

    def load_config(self):
        try:
            raw = solaros.storage.read_file(self.config_path(), 8192)
            data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("host", DEFAULT_HOST)
        data.setdefault("model", "")
        data.setdefault("stream", True)
        data.setdefault("follow", False)
        data.setdefault("system", "")
        data.setdefault("predict", 512)
        data.setdefault("dir", self.name)
        return data

    def save_config(self, config):
        self._write(self.config_path(), json.dumps(config))

    def chat_path(self, chat_id):
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        safe = "".join(ch for ch in str(chat_id) if ch in allowed)
        if not safe:
            safe = "chat"
        return self.root + "/chats/" + safe + ".json"

    def save_chat(self, chat):
        self._write(self.chat_path(chat["id"]), json.dumps(chat))

    def load_chat(self, chat_id):
        raw = solaros.storage.read_file(self.chat_path(chat_id), 256 * 1024)
        return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)

    def list_chats(self):
        found = []
        cursor = None
        while True:
            try:
                page = solaros.storage.scandir(self.root + "/chats", cursor, 16)
            except OSError:
                return found
            for entry in page.get("entries", []):
                name = entry.get("name", "")
                if not name.endswith(".json"):
                    continue
                try:
                    raw = solaros.storage.read_file(self.root + "/chats/" + name, 256 * 1024)
                    chat = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
                except (OSError, ValueError):
                    continue
                found.append(chat)
            cursor = page.get("next_cursor")
            if cursor is None:
                break
        found.sort(key=lambda item: item.get("updated", ""), reverse=True)
        return found

    def _write(self, path, text):
        handle = open(path, "w")
        try:
            handle.write(text)
        finally:
            handle.close()


class Ollama:
    def __init__(self, host):
        self.host = host.rstrip("/")

    def tags(self):
        payload = self._get("/api/tags")
        models = []
        for item in payload.get("models", []):
            name = item.get("name")
            if name:
                models.append(name)
        models.sort()
        return models

    def chat(self, model, messages, options=None):
        body = {"model": model, "messages": messages, "stream": False}
        if options:
            body["options"] = options
        payload = self._post("/api/chat", body)
        message = payload.get("message") or {}
        content = message.get("content", "")
        if not content:
            raise RuntimeError("model returned an empty reply")
        return content

    def open_chat(self, model, messages, options=None):
        body = {"model": model, "messages": messages, "stream": True}
        if options:
            body["options"] = options
        return solaros.http.stream_open(
            "POST",
            self.host + "/api/chat",
            json.dumps(body),
            {"Content-Type": "application/json", "Accept": "application/x-ndjson"},
            60000,
            False,
        )

    def read_pieces(self, handle, pending):
        event = solaros.http.stream_read(handle, 200)
        pieces = []
        done = False
        if not event:
            return pending, pieces, done
        kind = event.get("type")
        if kind == "data":
            pending += event.get("data") or b""
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                text = line.decode("utf-8").strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except ValueError:
                    raise RuntimeError("stream line was not JSON")
                if payload.get("error"):
                    raise RuntimeError(str(payload.get("error")))
                piece = (payload.get("message") or {}).get("content") or ""
                if piece:
                    pieces.append(piece)
                if payload.get("done"):
                    done = True
        elif kind == "error":
            detail = event.get("message") or event.get("error") or "stream failed"
            raise RuntimeError(str(detail))
        elif kind == "complete":
            done = True
        return pending, pieces, done

    def _get(self, path):
        response = solaros.http.get(
            self.host + path, None, TIMEOUT_MS, MAX_BYTES, False
        )
        return self._decode(response)

    def _post(self, path, body):
        response = solaros.http.post(
            self.host + path,
            json.dumps(body),
            {"Content-Type": "application/json", "Accept": "application/json"},
            TIMEOUT_MS,
            MAX_BYTES,
            False,
        )
        return self._decode(response)

    def _decode(self, response):
        status = response.get("status_code", 0)
        text = decode_body(response)
        if response.get("truncated"):
            raise RuntimeError("reply exceeded {} bytes".format(MAX_BYTES))
        if status != 200:
            detail = text.strip().replace("\n", " ")
            raise RuntimeError("HTTP {} {}".format(status, detail[:80]))
        try:
            return json.loads(text)
        except ValueError:
            raise RuntimeError("server did not return JSON")


def now_id():
    try:
        stamp = solaros.time.localtime()
        return "{:04d}{:02d}{:02d}-{:02d}{:02d}{:02d}".format(
            stamp[0], stamp[1], stamp[2], stamp[3], stamp[4], stamp[5]
        )
    except Exception:
        return "chat"


def blank_chat(model):
    chat_id = now_id()
    return {
        "id": chat_id,
        "title": "New chat",
        "model": model,
        "updated": chat_id,
        "messages": [],
    }


class App:
    def __init__(self, store, config):
        self.store = store
        self.config = config
        self.chat = blank_chat(config.get("model", ""))
        self.draft = ""
        self.cursor = 0
        self.scroll = 0
        self.pin = None
        self.following = False
        self.input_pinned = False
        self.pending_cut = None
        self.held = None
        self.status = "Esc menu"
        self.lines = []

    def client(self):
        host = self.config.get("host", "")
        if not host:
            raise RuntimeError("no server set")
        return Ollama(host)

    def run(self):
        if not self.config.get("host"):
            self.edit_host()
        if not self.config.get("model"):
            self.pick_model()
        self.chat["model"] = self.config.get("model", "")
        self.draw()
        while not solaros.should_exit():
            key = tui.getch(250)
            if key is None:
                continue
            if key == tui.KEY_ESCAPE:
                self.input_pinned = False
                if not self.menu():
                    return
            elif key in (tui.KEY_UP, tui.KEY_PAGE_UP):
                self.scroll = max(0, self.scroll - (1 if key == tui.KEY_UP else 5))
                self.input_pinned = False
            elif key in (tui.KEY_DOWN, tui.KEY_PAGE_DOWN):
                self.scroll += 1 if key == tui.KEY_DOWN else 5
                self.input_pinned = False
            elif key in (KEY_BACKSPACE, KEY_DELETE_CHAR):
                if self.cursor > 0:
                    self.draft = self.draft[: self.cursor - 1] + self.draft[self.cursor :]
                    self.cursor -= 1
                    self.note_input()
                continue
            elif key == tui.KEY_DELETE:
                if self.cursor < len(self.draft):
                    self.draft = self.draft[: self.cursor] + self.draft[self.cursor + 1 :]
                    self.note_input()
                continue
            elif key in (KEY_ENTER, KEY_RETURN):
                self.input_pinned = False
                self.send()
            elif key == tui.KEY_LEFT:
                self.cursor = max(0, self.cursor - 1)
                self.note_input()
                continue
            elif key == tui.KEY_RIGHT:
                self.cursor = min(len(self.draft), self.cursor + 1)
                self.note_input()
                continue
            elif key == tui.KEY_CTRL_LEFT:
                self.cursor = word_left(self.draft, self.cursor)
                self.note_input()
                continue
            elif key == tui.KEY_CTRL_RIGHT:
                self.cursor = word_right(self.draft, self.cursor)
                self.note_input()
                continue
            elif isinstance(key, int) and 32 <= key <= 126:
                if len(self.draft) < 800:
                    self.draft = self.draft[: self.cursor] + chr(key) + self.draft[self.cursor :]
                    self.cursor += 1
                    self.note_input()
                continue
            else:
                continue
            self.draw()

    def note_input(self):
        rows, cols = tui.size()
        height = self.prompt_height(rows, cols)
        if not self.input_pinned or height != getattr(self, "prompt_rows", 1):
            self.input_pinned = True
            self.prompt_rows = height
            self.pin = "bottom"
            self.draw()
            return
        self.paint_prompt()

    def prompt_view(self, rows, cols):
        width = max(1, cols - 2)
        lines = wrap_indexed(self.draft, width)
        cursor_line = len(lines) - 1
        for idx, (line, start) in enumerate(lines):
            if start <= self.cursor <= start + len(line):
                cursor_line = idx
                break
        max_rows = max(1, rows - 3)
        if len(lines) > max_rows:
            start_line = max(0, min(cursor_line, len(lines) - max_rows))
        else:
            start_line = 0
        shown = lines[start_line : start_line + max_rows]
        top = rows - len(shown)
        return top, shown, start_line, cursor_line

    def prompt_height(self, rows, cols):
        top, shown, _, _ = self.prompt_view(rows, cols)
        return len(shown)

    def send(self):
        text = self.draft.strip()
        if not text:
            return
        model = self.config.get("model", "")
        if not model:
            self.status = "pick a model first"
            return
        self.chat["model"] = model
        if self.pending_cut is not None:
            self.held = self.chat["messages"][self.pending_cut :]
            self.chat["messages"] = self.chat["messages"][: self.pending_cut]
        self.chat["messages"].append({"role": "user", "content": text})
        if self.chat["title"] == "New chat":
            self.chat["title"] = clip(text, TITLE_LEN)
        self.draft = ""
        self.cursor = 0
        if self.config.get("stream", True):
            self.send_stream(model)
        else:
            self.send_full(model)

    def request_messages(self):
        cleaned = []
        for message in self.chat["messages"]:
            content = message.get("content") or ""
            if not content:
                continue
            cleaned.append({"role": message.get("role", "user"), "content": content})
        if len(cleaned) > KEEP_MESSAGES:
            cleaned = cleaned[-KEEP_MESSAGES:]
        prompt = (self.config.get("system") or "").strip()
        if prompt:
            cleaned.insert(0, {"role": "system", "content": prompt})
        return cleaned

    def predict_options(self):
        try:
            limit = int(self.config.get("predict", 512))
        except (TypeError, ValueError):
            limit = 512
        if limit <= 0:
            return None
        return {"num_predict": limit}

    def finish_send(self):
        self.pending_cut = None
        self.held = None
        self.chat["updated"] = now_id()
        self.status = ""
        try:
            self.store.save_chat(self.chat)
        except Exception as exc:
            self.status = "save failed: {}".format(exc)

    def restore_prompt(self):
        if self.chat["messages"] and self.chat["messages"][-1].get("role") != "user":
            self.chat["messages"].pop()
        if self.chat["messages"] and self.chat["messages"][-1].get("role") == "user":
            self.draft = self.chat["messages"].pop().get("content") or ""
            self.cursor = len(self.draft)
            self.input_pinned = False
        if self.held:
            self.pending_cut = len(self.chat["messages"])
            self.chat["messages"].extend(self.held)
            self.held = None

    def send_full(self, model):
        self.status = "thinking..."
        self.pin = "bottom"
        self.draw()
        try:
            reply = self.client().chat(model, self.request_messages(), self.predict_options())
        except Exception as exc:
            self.restore_prompt()
            self.status = str(exc)
            self.draw()
            return
        self.chat["messages"].append({"role": "assistant", "content": reply, "model": model})
        self.finish_send()
        self.pin = "bottom" if self.config.get("follow") else "reply"
        self.draw()

    def poll_scroll(self):
        moved = False
        for _ in range(8):
            key = tui.getch(1)
            if key is None:
                break
            if key == tui.KEY_ESCAPE:
                return "stop"
            if key in (tui.KEY_UP, tui.KEY_PAGE_UP):
                self.scroll = max(0, self.scroll - (1 if key == tui.KEY_UP else 5))
                self.following = False
                moved = True
            elif key in (tui.KEY_DOWN, tui.KEY_PAGE_DOWN):
                self.scroll += 1 if key == tui.KEY_DOWN else 5
                self.following = False
                moved = True
        if moved:
            self.draw(False)
        return None

    def send_stream(self, model):
        self.chat["messages"].append({"role": "assistant", "content": "", "model": model})
        self.status = ""
        self.following = bool(self.config.get("follow"))
        self.pin = "bottom" if self.following else "reply"
        self.draw()
        handle = self.client().open_chat(model, self.request_messages(), self.predict_options())
        pending = b""
        try:
            while not solaros.should_exit():
                if self.poll_scroll() == "stop":
                    self.chat["messages"][-1]["content"] += "\n\nstopped by user"
                    self.finish_send()
                    self.draw(False)
                    return
                pending, pieces, done = self.client().read_pieces(handle, pending)
                if pieces:
                    reply = self.chat["messages"][-1]["content"] + "".join(pieces)
                    if len(reply) > MAX_BYTES:
                        self.chat["messages"][-1]["content"] = reply[:MAX_BYTES] + "\n\nstopped, reply too long"
                        self.finish_send()
                        self.draw(False)
                        return
                    self.chat["messages"][-1]["content"] = reply
                    if self.following:
                        self.pin = "bottom"
                    self.draw(False)
                if done:
                    break
        except Exception as exc:
            self.restore_prompt()
            self.status = str(exc)
            self.draw()
            return
        finally:
            solaros.http.stream_close(handle)
        if not self.chat["messages"][-1].get("content"):
            self.chat["messages"].pop()
            self.status = "model returned an empty reply"
            self.draw()
            return
        self.finish_send()
        self.draw(False)

    def menu_items(self):
        mode = "tokens" if self.config.get("stream", True) else "full answer"
        follow = "on" if self.config.get("follow") else "off"
        prompt = "set" if (self.config.get("system") or "").strip() else "empty"
        try:
            limit = int(self.config.get("predict", 512))
        except (TypeError, ValueError):
            limit = 512
        cap = "none" if limit <= 0 else str(limit)
        return [
            "Models",
            "Saved chats",
            "New chat",
            "Edit last",
            "Server",
            "Folder: " + self.store.name,
            "System: " + prompt,
            "Print: " + mode,
            "Follow: " + follow,
            "Tokens: " + cap,
            "Quit",
        ]

    def menu(self):
        items = self.menu_items()
        selected = 0
        self.draw_list("Menu", items, selected, "Enter select, Esc back")
        while not solaros.should_exit():
            key = tui.getch(250)
            if key is None:
                continue
            if key == tui.KEY_ESCAPE:
                return True
            if key == tui.KEY_UP:
                selected = (selected - 1) % len(items)
            elif key == tui.KEY_DOWN:
                selected = (selected + 1) % len(items)
            elif key in (KEY_ENTER, KEY_RETURN):
                choice = items[selected]
                if choice == "Quit":
                    return False
                if choice == "New chat":
                    self.chat = blank_chat(self.config.get("model", ""))
                    self.draft = ""
                    self.cursor = 0
                    self.pending_cut = None
                    self.held = None
                    self.scroll = 0
                    self.status = "new chat"
                    return True
                if choice == "Edit last":
                    self.edit_last()
                    return True
                if choice == "Server":
                    self.edit_host()
                    return True
                if choice.startswith("Folder:"):
                    self.edit_folder()
                    items = self.menu_items()
                if choice == "Models":
                    self.pick_model()
                    return True
                if choice == "Saved chats":
                    self.pick_chat()
                    return True
                if choice.startswith("System:"):
                    self.edit_system()
                    items = self.menu_items()
                elif choice.startswith("Print:"):
                    self.config["stream"] = not self.config.get("stream", True)
                    self.store.save_config(self.config)
                    items = self.menu_items()
                elif choice.startswith("Follow:"):
                    self.config["follow"] = not self.config.get("follow")
                    self.store.save_config(self.config)
                    items = self.menu_items()
                elif choice.startswith("Tokens:"):
                    self.cycle_tokens()
                    items = self.menu_items()
                else:
                    continue
            elif isinstance(key, int) and key in (ord("q"), ord("Q")):
                return False
            else:
                continue
            self.draw_list("Menu", items, selected, "Enter select, Esc back")
        return False

    def pick_model(self):
        self.status = "loading models"
        self.draw()
        try:
            models = self.client().tags()
        except Exception as exc:
            self.status = str(exc)
            return
        if not models:
            self.status = "server has no models"
            return
        current = self.config.get("model", "")
        selected = models.index(current) if current in models else 0
        self.draw_list("Models", models, selected, self.config.get("host", ""))
        while not solaros.should_exit():
            key = tui.getch(250)
            if key is None:
                continue
            if key == tui.KEY_ESCAPE:
                return
            if key == tui.KEY_UP:
                selected = (selected - 1) % len(models)
            elif key == tui.KEY_DOWN:
                selected = (selected + 1) % len(models)
            elif key in (KEY_ENTER, KEY_RETURN):
                self.config["model"] = models[selected]
                self.store.save_config(self.config)
                self.chat["model"] = models[selected]
                self.status = models[selected]
                return
            else:
                continue
            self.draw_list("Models", models, selected, self.config.get("host", ""))

    def pick_chat(self):
        chats = self.store.list_chats()
        if not chats:
            self.status = "no saved chats"
            return
        labels = [clip(item.get("title", item.get("id", "?")), 50) for item in chats]
        selected = 0
        self.draw_list("Saved chats", labels, selected, "Enter open")
        while not solaros.should_exit():
            key = tui.getch(250)
            if key is None:
                continue
            if key == tui.KEY_ESCAPE:
                return
            if key == tui.KEY_UP:
                selected = (selected - 1) % len(chats)
            elif key == tui.KEY_DOWN:
                selected = (selected + 1) % len(chats)
            elif key in (KEY_ENTER, KEY_RETURN):
                self.chat = chats[selected]
                self.chat.setdefault("messages", [])
                self.config["model"] = self.chat.get("model", self.config.get("model", ""))
                self.store.save_config(self.config)
                self.draft = ""
                self.cursor = 0
                self.pending_cut = None
                self.held = None
                self.pin = "reply"
                self.status = "opened " + self.chat.get("id", "")
                return
            else:
                continue
            self.draw_list("Saved chats", labels, selected, "Enter open")

    def edit_host(self):
        value = self.config.get("host", "") or DEFAULT_HOST
        self.draw_prompt("Ollama server", value, "Enter save, Esc cancel")
        while not solaros.should_exit():
            key = tui.getch(250)
            if key is None:
                continue
            if key == tui.KEY_ESCAPE:
                return
            if key in (KEY_BACKSPACE, KEY_DELETE_CHAR, tui.KEY_DELETE):
                value = value[:-1]
            elif key in (KEY_ENTER, KEY_RETURN):
                host = value.strip().rstrip("/")
                if not (host.startswith("http://") or host.startswith("https://")):
                    self.status = "URL must start with http://"
                    return
                self.config["host"] = host
                self.store.save_config(self.config)
                self.status = host
                return
            elif isinstance(key, int) and 32 <= key <= 126:
                value += chr(key)
            else:
                continue
            self.draw_prompt("Ollama server", value, "Enter save, Esc cancel")

    def edit_last(self):
        messages = self.chat.get("messages") or []
        index = None
        for position in range(len(messages) - 1, -1, -1):
            if messages[position].get("role") == "user" and messages[position].get("content"):
                index = position
                break
        if index is None:
            self.status = "no prompt to edit"
            return
        self.draft = messages[index].get("content") or ""
        self.cursor = len(self.draft)
        self.pending_cut = index
        self.pin = "bottom"
        self.status = ""

    def edit_folder(self):
        value = self.store.name
        self.draw_prompt("Card folder", value, "Enter save, Esc cancel")
        while not solaros.should_exit():
            key = tui.getch(250)
            if key is None:
                continue
            if key == tui.KEY_ESCAPE:
                return
            if key in (KEY_BACKSPACE, KEY_DELETE_CHAR, tui.KEY_DELETE):
                value = value[:-1]
            elif key in (KEY_ENTER, KEY_RETURN):
                name = safe_name(value, "")
                if not name:
                    self.status = "folder needs a name"
                    return
                self.store = Store(name)
                self.config["dir"] = name
                self.store.save_config(self.config)
                self.status = self.store.root
                return
            elif isinstance(key, int) and 32 <= key <= 126:
                if len(value) < 24:
                    value += chr(key)
            else:
                continue
            self.draw_prompt("Card folder", value, "Enter save, Esc cancel")

    def edit_system(self):
        value = self.config.get("system") or ""
        self.draw_prompt("System prompt", value, "Enter save, Esc cancel")
        while not solaros.should_exit():
            key = tui.getch(250)
            if key is None:
                continue
            if key == tui.KEY_ESCAPE:
                return
            if key in (KEY_BACKSPACE, KEY_DELETE_CHAR, tui.KEY_DELETE):
                value = value[:-1]
            elif key in (KEY_ENTER, KEY_RETURN):
                self.config["system"] = value.strip()
                self.store.save_config(self.config)
                self.status = ""
                return
            elif isinstance(key, int) and 32 <= key <= 126:
                if len(value) < 400:
                    value += chr(key)
            else:
                continue
            self.draw_prompt("System prompt", value, "Enter save, Esc cancel")

    def cycle_tokens(self):
        order = [512, 1024, 2048, 4096, 0]
        try:
            current = int(self.config.get("predict", 512))
        except (TypeError, ValueError):
            current = 512
        if current not in order:
            current = 512
        self.config["predict"] = order[(order.index(current) + 1) % len(order)]
        self.store.save_config(self.config)

    def transcript(self, width):
        lines = []
        reply_at = 0
        messages = self.chat.get("messages", [])
        if not messages:
            lines.append("No messages yet. Type, then Enter.")
            return lines, 0
        for message in messages:
            if message.get("role") == "user":
                role = "you"
            else:
                role = message.get("model") or self.chat.get("model") or "model"
                reply_at = len(lines)
            lines.append(role)
            lines.extend(wrap(message.get("content", ""), width))
            lines.append("")
        return lines, reply_at

    def draw(self, clear=True):
        rows, cols = tui.size()
        if clear:
            tui.clear()
        model = self.config.get("model") or "no model"
        header = clip("{}  {}".format(model, self.chat.get("title", "")), cols)
        tui.addstr(0, 0, header, tui.INVERSE)
        footer = pad(self.status, cols)
        top, shown, start_line, cursor_line = self.prompt_view(rows, cols)
        self.prompt_rows = len(shown)
        tui.addstr(top - 1, 0, footer, tui.BOLD)
        self.write_prompt(shown, start_line, cursor_line, top, cols)
        height = max(1, top - 2)
        width = max(1, cols)
        self.lines, reply_at = self.transcript(width)
        if self.pin == "reply":
            self.scroll = reply_at
        elif self.pin == "bottom":
            self.scroll = 10**6
        self.pin = None
        max_scroll = max(0, len(self.lines) - height)
        if self.scroll > max_scroll:
            self.scroll = max_scroll
        view = self.lines[self.scroll : self.scroll + height]
        for index in range(height):
            line = view[index] if index < len(view) else ""
            tui.addstr(1 + index, 0, pad(line, cols))
        tui.refresh()

    def write_prompt(self, shown, start_line, cursor_line, top, cols):
        for offset, (line, start) in enumerate(shown):
            prefix = "> " if offset == 0 and start_line == 0 else "  "
            row = top + offset
            if start_line + offset != cursor_line:
                tui.addstr(row, 0, pad(prefix + line, cols))
                continue
            col = self.cursor - start
            if col < 0:
                col = 0
            if col > len(line):
                col = len(line)
            before = prefix + line[:col]
            mark = line[col] if col < len(line) else " "
            after = line[col + 1 :] if col < len(line) else ""
            tui.addstr(row, 0, pad(before, cols))
            if len(before) < cols:
                tui.addstr(row, len(before), mark, tui.INVERSE)
            rest = after
            rest_col = len(before) + 1
            if rest and rest_col < cols:
                tui.addstr(row, rest_col, pad(rest, cols - rest_col))

    def paint_prompt(self):
        rows, cols = tui.size()
        top, shown, start_line, cursor_line = self.prompt_view(rows, cols)
        self.write_prompt(shown, start_line, cursor_line, top, cols)
        tui.refresh()

    def draw_list(self, title, items, selected, hint):
        rows, cols = tui.size()
        tui.clear()
        tui.addstr(0, 0, clip(title, cols), tui.INVERSE)
        tui.addstr(rows - 1, 0, clip(hint, cols), tui.BOLD)
        height = max(1, rows - 2)
        start = 0
        if selected >= height:
            start = selected - height + 1
        for index in range(height):
            item_index = start + index
            if item_index >= len(items):
                break
            attr = tui.INVERSE if item_index == selected else tui.NORMAL
            tui.addstr(1 + index, 0, clip(items[item_index], cols), attr)
        tui.refresh()

    def draw_prompt(self, title, value, hint):
        rows, cols = tui.size()
        tui.clear()
        tui.addstr(0, 0, clip(title, cols), tui.INVERSE)
        lines = wrap(value, max(1, cols))
        height = max(1, rows - 2)
        for index in range(height):
            line = lines[index] if index < len(lines) else ""
            tui.addstr(1 + index, 0, pad(line, cols))
        tui.addstr(rows - 1, 0, clip(hint, cols), tui.BOLD)
        tui.refresh()


def open_store():
    preferred = Store(APP)
    if solaros.storage.exists(preferred.config_path()):
        return preferred
    legacy = Store("ollama")
    if solaros.storage.exists(legacy.config_path()):
        return legacy
    return preferred


def main():
    store = open_store()
    config = store.load_config()
    if len(sys.argv) > 1:
        config["host"] = sys.argv[1].strip().rstrip("/")
        store.save_config(config)
    solaros.tick_interval(20)
    try:
        App(store, config).run()
    except KeyboardInterrupt:
        pass
    finally:
        tui.clear()
        tui.refresh()


main()
