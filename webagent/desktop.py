"""Windows desktop apps through UI Automation (pywinauto), shown to the model like a web page: a list of elements
with refs (d1, d2, ...).

Safety by design:
- Only apps on the allow list (WA_DESKTOP_APPS) can be opened.
- The agent can only see and touch the windows it opened itself (and their dialogs), never your other windows.
- Password fields are refused; risky buttons (Save, Delete, Send...) and keys (Ctrl+S, Alt+F4...) need approval
  (the same safety gate as the browser).
"""
from __future__ import annotations

import io
import logging
import warnings
import re
import subprocess
import sys
import time

from webagent.config import settings

log = logging.getLogger(__name__)

# name -> (how to start it, title of its main window)
APPS: dict[str, tuple[str, str]] = {
    "notepad": ("notepad.exe", r".*Notepad$"),
    "calculator": ("calc.exe", r"^Calculator$"),
    "paint": ("mspaint.exe", r".*Paint$"),
    "wordpad": ("write.exe", r".*WordPad$"),
    "excel": ("excel.exe", r".*Excel$"),
    "word": ("winword.exe", r".*Word$"),
}
SHARED_HOST = {"calculator"}
ALIASES = {"calc": "calculator", "ms paint": "paint", "mspaint": "paint", "microsoft excel": "excel",
           "microsoft word": "word", "text editor": "notepad"}
LISTED = {"Button", "Edit", "Document", "MenuItem", "ListItem", "TabItem", "CheckBox", "RadioButton", "ComboBox",
          "Hyperlink", "TreeItem", "DataItem", "SplitButton", "Spinner", "Slider", "Text", "Custom", "Header",
          "HeaderItem"}
TYPABLE = {"Edit", "Document", "ComboBox"}
RISKY_KEYS = re.compile(r"(\^s|\^p|\^w|%\{?f4\}?|\^\+s|\{del(ete)?\})", re.I)


class DesktopError(Exception):
    """Shown to the model so it can try something else."""


def available() -> bool:
    return sys.platform == "win32" and settings.desktop


def allowed_apps() -> list[str]:
    return [a for a in settings.desktop_apps if a in APPS]


def app_key(name: str) -> str | None:
    n = re.sub(r"\s+", " ", (name or "").strip().lower()).removesuffix(".exe")
    n = ALIASES.get(n, n)
    return n if n in allowed_apps() else None


class DesktopSession:
    def __init__(self):
        if not available():
            raise DesktopError("Desktop apps are not available here (only on Windows with WA_DESKTOP=1).")
        try:
            import comtypes

            comtypes.CoInitialize()  # needed when the task runs in a thread
        except Exception:
            pass
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # "Revert to STA COM threading mode"
            from pywinauto import Desktop

        self._desktop = Desktop(backend="uia")
        self.windows: dict[int, dict] = {}   # handle -> {app, pid, title}
        self.active: int | None = None
        self.refs: dict[str, object] = {}
        self.info: dict[str, dict] = {}

    # -------------------------------------------------------------- windows
    def _top(self):
        try:
            return self._desktop.windows()
        except Exception as exc:
            raise DesktopError(f"Could not list windows: {exc}") from exc

    def open(self, name: str) -> str:
        key = app_key(name)
        if not key:
            raise DesktopError(f"{name!r} is not on the allowed apps list: {', '.join(allowed_apps())}.")
        exe, title_re = APPS[key]
        for w in self._refresh():  # already open by this task: use that window, do not start a second one
            if self.windows.get(w.handle, {}).get("app") == key:
                self.active = w.handle
                self._focus()
                return f"{key} is already open: window \"{w.window_text()}\".\n" + self.read()
        before = {w.handle for w in self._top()}
        subprocess.Popen(["cmd", "/c", "start", "", exe], shell=False, creationflags=0x08000000)  # no console
        deadline = time.time() + 20
        while time.time() < deadline:
            for w in self._top():
                try:
                    if w.handle not in before and re.match(title_re, w.window_text() or ""):
                        self.windows[w.handle] = {"app": key, "pid": w.process_id(), "title": w.window_text()}
                        self.active = w.handle
                        self._focus()
                        return f"Opened {key}: window \"{w.window_text()}\".\n" + self.read()
                except Exception:
                    continue
            time.sleep(0.5)
        raise DesktopError(f"{key} did not open a window in 20 seconds.")

    def _refresh(self) -> list:
        """The agent's windows: the ones it opened, plus new dialogs of the same programs."""
        # Store apps (Calculator) share one host process with other apps: only their own window counts.
        pids = {w["pid"] for w in self.windows.values() if w["app"] not in SHARED_HOST}
        alive = []
        for w in self._top():
            try:
                h = w.handle
                if h in self.windows:
                    self.windows[h]["title"] = w.window_text()
                    alive.append(w)
                elif w.process_id() in pids:
                    app = next(x["app"] for x in self.windows.values() if x["pid"] == w.process_id())
                    self.windows[h] = {"app": app, "pid": w.process_id(), "title": w.window_text()}
                    self.active = h  # a new dialog (Save as, a question) comes to the front
                    alive.append(w)
            except Exception:
                continue
        live = {w.handle for w in alive}
        for h in list(self.windows):
            if h not in live:
                self.windows.pop(h)
        if self.active not in live:
            self.active = next(iter(live), None)
        return alive

    def _window(self):
        self._refresh()
        if self.active is None:
            raise DesktopError("No app window is open. Use desktop_open first.")
        for w in self._top():
            if w.handle == self.active:
                return w
        raise DesktopError("The app window was closed.")

    def _focus(self) -> None:
        try:
            self._window().set_focus()
        except Exception:
            pass

    def switch(self, index: int) -> str:
        wins = self._refresh()
        if not 1 <= index <= len(wins):
            raise DesktopError(f"There are {len(wins)} app windows; use 1 to {len(wins)}.")
        self.active = wins[index - 1].handle
        self._focus()
        return self.read()

    # -------------------------------------------------------------- observe
    def read(self, max_items: int = 150) -> str:
        win = self._window()
        self.refs, self.info = {}, {}
        lines = []
        wins = self._refresh()
        if len(wins) > 1:
            lines.append("App windows: " + " | ".join(
                f"{i + 1}. {w.window_text()}" + (" (active)" if w.handle == self.active else "")
                for i, w in enumerate(wins)))
        lines.append(f"Window: {win.window_text()}  [{self.windows.get(win.handle, {}).get('app', '?')}]")
        queue, visited, n = [win], 0, 0
        while queue and n < max_items and visited < 2500:
            el = queue.pop(0)
            visited += 1
            try:
                kids = el.children()
            except Exception:
                kids = []
            queue.extend(kids)
            if el is win:
                continue
            try:
                ei = el.element_info
                ctype, name = ei.control_type or "", (ei.name or "").strip()
                if ctype not in LISTED or not el.is_visible():
                    continue
                value = ""
                if ctype in TYPABLE:
                    value = self._value(el)
                if not name and not value and ctype in ("Text", "Custom", "Header"):
                    continue
                if ctype == "Text" and len(name) > 300:
                    name = name[:300] + "..."
                n += 1
                ref = f"d{n}"
                self.refs[ref] = el
                self.info[ref] = {"role": ctype.lower(), "name": name[:120], "password": self._is_password(el)}
                line = f"{ref} [{ctype}] \"{name[:120]}\""
                if value and not self.info[ref]["password"]:
                    line += f" value=\"{value[:400]}\""
                if not ei.enabled:
                    line += " (disabled)"
                lines.append(line)
            except Exception:
                continue
        if n >= max_items:
            lines.append(f"(only the first {max_items} elements are shown)")
        return "\n".join(lines)

    @staticmethod
    def _value(el) -> str:
        try:
            v = el.iface_value.CurrentValue
        except Exception:
            try:
                v = el.window_text()
                v = "" if v == (el.element_info.name or "") else v  # some controls repeat their name
            except Exception:
                v = ""
        return str(v or "").replace("\r\n", "\n").replace("\n", " / ")

    @staticmethod
    def _is_password(el) -> bool:
        try:
            return bool(el.element_info.element.CurrentIsPassword)
        except Exception:
            return False

    def element(self, ref: str) -> dict:
        ref = ref.strip()
        if ref not in self.info:
            raise DesktopError(f"There is no element {ref}. Use a ref from the latest desktop_read.")
        return self.info[ref]

    def screenshot(self, quality: int = 60) -> bytes | None:
        try:
            img = self._window().capture_as_image()
            buf = io.BytesIO()
            img.convert("RGB").save(buf, "JPEG", quality=quality)
            return buf.getvalue()
        except Exception:
            return None

    # -------------------------------------------------------------- act
    def click(self, ref: str) -> str:
        self.element(ref)
        el = self.refs[ref]
        self._focus()
        try:
            try:
                el.invoke()
            except Exception:
                try:
                    el.select()
                except Exception:
                    el.click_input()
        except Exception as exc:
            raise DesktopError(f"Could not click {ref}: {str(exc).splitlines()[0][:200]}") from exc
        time.sleep(0.6)
        return f"Clicked {ref}.\n" + self._after()

    def type(self, ref: str, text: str, enter: bool = False) -> str:
        info = self.element(ref)
        if info["role"] not in {t.lower() for t in TYPABLE}:
            raise DesktopError(f"{ref} is a {info['role']}, not a text field.")
        el = self.refs[ref]
        self._focus()
        try:
            try:
                el.iface_value.SetValue(text)
            except Exception:
                try:
                    el.set_edit_text(text)
                except Exception:
                    el.set_focus()
                    from pywinauto.keyboard import send_keys

                    send_keys("^a{BACKSPACE}")
                    el.type_keys(text, with_spaces=True, with_newlines=True, with_tabs=True, pause=0.01)
            if enter:
                from pywinauto.keyboard import send_keys

                el.set_focus()
                send_keys("{ENTER}")
        except Exception as exc:
            raise DesktopError(f"Could not type into {ref}: {str(exc).splitlines()[0][:200]}") from exc
        time.sleep(0.4)
        return f"Typed into {ref}.\n" + self._after()

    def _after(self) -> str:
        return self.read() if self._refresh() else "No app windows are open any more."

    def keys(self, keys: str) -> str:
        from pywinauto.keyboard import send_keys

        self._focus()
        try:
            send_keys(keys, pause=0.02, with_spaces=True)
        except Exception as exc:
            raise DesktopError(f"Could not press {keys}: {str(exc).splitlines()[0][:200]}") from exc
        time.sleep(0.6)
        return f"Pressed {keys}.\n" + self._after()

    def close_window(self) -> str:
        win = self._window()
        title = win.window_text()
        try:
            win.close()
        except Exception as exc:
            raise DesktopError(f"Could not close {title}: {exc}") from exc
        time.sleep(0.8)
        wins = self._refresh()
        if wins:
            return f"Closed \"{title}\". Now active:\n" + self.read()
        return f"Closed \"{title}\". No app windows are open."

    def close_all(self) -> None:
        """At the end of a task, close what the agent opened (unsaved changes keep their window and its question)."""
        for w in self._refresh():
            try:
                w.close()
            except Exception:
                pass
