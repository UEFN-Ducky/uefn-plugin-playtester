"""Linux: game windows, focus, keys, mouse and pixels through the X server (python-xlib).

UEFN and the Fortnite client run under Proton/Wine, which draws them as X11 windows (through
XWayland on a Wayland desktop), so on Linux everything goes through X: a window id (xid) plays
the part of a Windows hwnd. Keys and the mouse go in with XTest, or xdotool when the server has
no XTEST. Nothing here runs on Windows; the modules pick it when a call happens.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from typing import Any

# Key names (inputs.SCAN) to X keysyms; letters and digits are their own lowercase name.
KEYSYMS = {"ESC": "Escape", "TAB": "Tab", "ENTER": "Return", "CTRL": "Control_L", "SHIFT": "Shift_L",
           "SPACE": "space", "ARROWUP": "Up", "ARROWDOWN": "Down", "ARROWLEFT": "Left", "ARROWRIGHT": "Right",
           "ALT": "Alt_L"}
# SendInput mouse flags (inputs.BUTTONS) to (X button, pressed).
BUTTON_FLAGS = {0x0002: (1, True), 0x0004: (1, False), 0x0008: (3, True), 0x0010: (3, False)}

_display: Any = None


def display() -> Any:
    """The shared X connection, or one clear error naming what is missing."""
    global _display
    if _display is not None:
        return _display
    if not os.environ.get("DISPLAY"):
        raise RuntimeError("Play Tester needs an X display on Linux and DISPLAY is not set: turn on XWayland "
                           "(Proton draws UEFN and Fortnite through it) or sign in to an X11 session.")
    try:
        from Xlib import display as xdisplay
    except ImportError as exc:
        raise RuntimeError("Play Tester needs python-xlib on Linux: update UEFN Ducky.") from exc
    try:
        _display = xdisplay.Display()
    except Exception as exc:
        raise RuntimeError(f"Play Tester could not open the X display {os.environ.get('DISPLAY')}: {exc}") from exc
    return _display


def _atom(name: str) -> int:
    return display().intern_atom(name)


def _win(xid: int) -> Any:
    return display().create_resource_object("window", int(xid))


def _root() -> Any:
    return display().screen().root


def _prop(w: Any, name: str) -> Any:
    from Xlib import X

    p = w.get_full_property(_atom(name), X.AnyPropertyType)
    return p.value if p is not None else None


def exe_of(pid: int) -> str:
    """The program's file name; for a Wine process its Windows .exe (FortniteClient-Win64-Shipping.exe)."""
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as fh:
            first = fh.read().split(b"\0", 1)[0].decode("utf-8", "replace")
        if first:
            return re.split(r"[\\/]", first)[-1]
        return os.path.basename(os.readlink(f"/proc/{int(pid)}/exe"))
    except (OSError, ValueError):
        return ""


def _title(w: Any) -> str:
    name = w.get_full_property(_atom("_NET_WM_NAME"), _atom("UTF8_STRING"))
    if name is not None:
        return name.value.decode("utf-8", "replace") if isinstance(name.value, bytes) else str(name.value)
    old = w.get_wm_name()
    return old.decode("latin-1") if isinstance(old, bytes) else str(old or "")


def _top(xid: int) -> int:
    """The root's child a window sits in (the focus can be a child of the game's window)."""
    root = _root().id
    w = _win(xid)
    while True:
        parent = w.query_tree().parent
        if parent is None or int(parent.id) in (0, root):
            return int(w.id)
        w = parent


def foreground() -> int:
    v = _prop(_root(), "_NET_ACTIVE_WINDOW")
    if v is not None and len(v) and int(v[0]):
        return int(v[0])
    focus = display().get_input_focus().focus  # no window manager: the X input focus
    if isinstance(focus, int) or focus is None or int(focus.id) <= 1:
        return 0
    try:
        return _top(int(focus.id))
    except Exception:
        return 0


def is_minimized(xid: int) -> bool:
    state = _prop(_win(xid), "_NET_WM_STATE")
    return bool(state is not None and _atom("_NET_WM_STATE_HIDDEN") in state)


def list_windows() -> list[dict[str, Any]]:
    """Every titled top-level window, the same rows as on Windows (hwnd = the X window id)."""
    from Xlib import X

    root = _root()
    clients = _prop(root, "_NET_CLIENT_LIST")
    xids = [int(x) for x in clients] if clients is not None else [int(c.id) for c in root.query_tree().children]
    front = foreground()
    rows: list[dict[str, Any]] = []
    for xid in xids:
        w = _win(xid)
        try:
            minimized = is_minimized(xid)
            if w.get_attributes().map_state != X.IsViewable and not minimized:
                continue
            title = _title(w)
            if not title:
                continue
            geo = w.get_geometry()
            pid = _prop(w, "_NET_WM_PID")
        except Exception:
            continue  # closed while we looked
        pid_n = int(pid[0]) if pid is not None and len(pid) else 0
        rows.append({"hwnd": xid, "title": title, "exe": exe_of(pid_n) if pid_n else "", "pid": pid_n,
                     "width": int(geo.width), "height": int(geo.height), "minimized": minimized, "foreground": xid == front})
    return rows


def client_box(xid: int) -> tuple[int, int, int, int]:
    """The window's drawing area on screen: (left, top, right, bottom)."""
    w = _win(xid)
    geo = w.get_geometry()
    pos = _root().translate_coords(w, 0, 0)
    return (int(pos.x), int(pos.y), int(pos.x) + int(geo.width), int(pos.y) + int(geo.height))


def window_image(xid: int) -> Any:
    """The window's own pixels as an RGB PIL image (XGetImage on the window), or None."""
    from PIL import Image
    from Xlib import X

    w = _win(xid)
    geo = w.get_geometry()
    if geo.width <= 0 or geo.height <= 0:
        return None
    raw = w.get_image(0, 0, geo.width, geo.height, X.ZPixmap, 0xFFFFFFFF)
    return Image.frombuffer("RGB", (geo.width, geo.height), raw.data, "raw", "BGRX", 0, 1)


def activate(xid: int) -> None:
    """Ask the window manager to bring it forward (EWMH), and raise and focus it ourselves."""
    from Xlib import X, protocol

    d, w, root = display(), _win(xid), _root()
    w.map()
    msg = protocol.event.ClientMessage(window=w, client_type=_atom("_NET_ACTIVE_WINDOW"),
                                       data=(32, [2, X.CurrentTime, 0, 0, 0]))  # 2 = asked by a pager / tool
    root.send_event(msg, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
    w.configure(stack_mode=X.Above)
    try:
        w.set_input_focus(X.RevertToParent, X.CurrentTime)
    except Exception:
        pass  # not viewable yet: the window manager's activate does it
    d.sync()


def _xdotool(*args: Any) -> None:
    subprocess.run(["xdotool", *[str(a) for a in args]], check=True, capture_output=True, timeout=5)


class XSender:
    """inputs.Sender's four calls on Linux: XTest, else xdotool."""

    def __init__(self, scan_names: dict[int, str]) -> None:
        self._names = dict(scan_names)
        d = display()
        self._xtest = bool(d.query_extension("XTEST"))
        if not self._xtest and not shutil.which("xdotool"):
            raise RuntimeError("Play Tester can't press keys: this X server has no XTEST and xdotool isn't installed "
                               "(sudo apt install xdotool).")

    def key_sym(self, sym: str, up: bool) -> None:
        if not self._xtest:
            _xdotool("keyup" if up else "keydown", sym)
            return
        from Xlib import X, XK
        from Xlib.ext import xtest

        d = display()
        code = d.keysym_to_keycode(XK.string_to_keysym(sym))
        if not code:
            raise RuntimeError(f"No key types {sym} on this keyboard layout")
        xtest.fake_input(d, X.KeyRelease if up else X.KeyPress, code)
        d.sync()

    def key(self, scan: int, up: bool) -> None:
        name = self._names.get(int(scan), "")
        if not name:
            raise ValueError(f"No key for scan code {scan:#x}")
        self.key_sym(KEYSYMS.get(name, name.lower()), up)

    def _motion(self, relative: bool, x: int, y: int) -> None:
        from Xlib import X
        from Xlib.ext import xtest

        d = display()
        xtest.fake_input(d, X.MotionNotify, detail=1 if relative else 0, x=int(x), y=int(y))
        d.sync()

    def move_to(self, x: int, y: int) -> None:
        if self._xtest:
            self._motion(False, x, y)
        else:
            _xdotool("mousemove", int(x), int(y))

    def move(self, dx: int, dy: int) -> None:
        if self._xtest:
            self._motion(True, dx, dy)
        else:
            _xdotool("mousemove_relative", "--", int(dx), int(dy))

    def button(self, flag: int) -> None:
        number, pressed = BUTTON_FLAGS[int(flag)]
        if not self._xtest:
            _xdotool("mousedown" if pressed else "mouseup", number)
            return
        from Xlib import X
        from Xlib.ext import xtest

        d = display()
        xtest.fake_input(d, X.ButtonPress if pressed else X.ButtonRelease, number)
        d.sync()

    def foreground(self) -> int:
        return foreground()


class XWin:
    """focus.Win's calls on Linux, so bring_to_front runs the same steps."""

    def __init__(self) -> None:
        from .inputs import SCAN

        self.s = XSender({v: k for k, v in SCAN.items()})

    def foreground(self) -> int:
        return foreground()

    def is_minimized(self, xid: int) -> bool:
        return is_minimized(xid)

    def restore(self, xid: int) -> None:
        activate(xid)

    def switch(self, xid: int) -> None:
        activate(xid)

    def attach_switch(self, xid: int) -> None:
        activate(xid)

    def alt_tap(self) -> None:
        self.s.key_sym("Alt_L", False)
        self.s.key_sym("Alt_L", True)

    def has_title_bar(self, xid: int) -> bool:
        ext = _prop(_win(xid), "_NET_FRAME_EXTENTS")  # left, right, top, bottom of the WM's frame
        return bool(ext is not None and len(ext) == 4 and int(ext[2]) > 0)

    def title_point(self, xid: int) -> tuple[int, int]:
        left, top, right, _ = client_box(xid)
        ext = _prop(_win(xid), "_NET_FRAME_EXTENTS")
        bar = int(ext[2]) if ext is not None and len(ext) == 4 else 8
        return (left + (right - left) // 2, top - max(4, bar // 2))

    def middle_point(self, xid: int) -> tuple[int, int]:
        left, top, right, bottom = client_box(xid)
        return (left + (right - left) // 2, top + (bottom - top) // 2)

    def cursor(self) -> tuple[int, int]:
        p = _root().query_pointer()
        return (int(p.root_x), int(p.root_y))

    def set_cursor(self, x: int, y: int) -> None:
        self.s.move_to(x, y)

    def click(self, button: str) -> None:
        import time

        from .inputs import BUTTONS

        down, up = BUTTONS[button]
        self.s.button(down)
        time.sleep(0.04)
        self.s.button(up)


def prefix_file(rel: str) -> str:
    """A file under the Proton prefix's AppData/Local (UEFN's and Fortnite's logs); '' when none is found."""
    try:
        from backend.tools.uefn.proton import find_prefix, local_appdata_dirs

        bases = [str(p) for p in local_appdata_dirs(find_prefix())]
    except Exception:  # an app without Proton support, or no prefix: look in the usual places
        home = os.path.expanduser("~")
        bases = sorted(glob.glob(f"{home}/.local/share/Steam/steamapps/compatdata/*/pfx/drive_c/users/*/AppData/Local")
                       + glob.glob(f"{home}/Games/Heroic/Prefixes/*/*/drive_c/users/*/AppData/Local")
                       + glob.glob(f"{home}/Games/Heroic/Prefixes/*/drive_c/users/*/AppData/Local"))
    hits = [os.path.join(b, rel) for b in bases if os.path.isfile(os.path.join(b, rel))]
    return max(hits, key=os.path.getmtime) if hits else ""
