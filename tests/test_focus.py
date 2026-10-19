"""Bringing Fortnite to the front: every path with a fake Windows."""

from __future__ import annotations

from uefn_plugin_playtester.focus import bring_to_front

H = 77


class FakeWin:
    """works = the first method that brings the window up ('plain', 'attach', 'alt', 'click' or None)."""

    def __init__(self, works=None, front=False, minimized=False, titled=False):
        self.works, self.fg, self.min, self.titled = works, (H if front else 5), minimized, titled
        self.calls, self.cursor_at = [], (10, 20)

    def foreground(self):
        return self.fg

    def is_minimized(self, h):
        return self.min

    def restore(self, h):
        self.calls.append("restore")
        self.min = False

    def _maybe(self, name):
        self.calls.append(name)
        if self.works == name:
            self.fg = H

    def switch(self, h):
        self._maybe("plain" if "alt-tap" not in self.calls else "alt")

    def attach_switch(self, h):
        self._maybe("attach")

    def alt_tap(self):
        self.calls.append("alt-tap")

    def has_title_bar(self, h):
        return self.titled

    def title_point(self, h):
        return (500, 8)

    def middle_point(self, h):
        return (960, 540)

    def cursor(self):
        return self.cursor_at

    def set_cursor(self, x, y):
        self.calls.append(("cursor", x, y))
        self.cursor_at = (x, y)

    def click(self, button):
        self.calls.append(("click", button))
        if self.works == "click":
            self.fg = H


def run(win, **kw):
    return bring_to_front(H, win=win, sleep=lambda s: None, **kw)


def test_already_in_front_does_nothing():
    w = FakeWin(front=True)
    assert run(w)["method"] == "already" and w.calls == []


def test_plain_switch_and_restore_only_when_minimized():
    w = FakeWin("plain", minimized=True)
    out = run(w)
    assert out["ok"] and out["method"] == "plain" and w.calls[0] == "restore"
    w2 = FakeWin("plain")
    run(w2)
    assert "restore" not in w2.calls


def test_attach_then_alt():
    assert run(FakeWin("attach"))["method"] == "attach"
    out = run(FakeWin("alt"))
    assert out["method"] == "alt" and out["tried"] == ["plain", "attach", "alt"]


def test_click_on_title_bar_when_windowed_and_cursor_goes_back():
    w = FakeWin("click", titled=True)
    out = run(w)
    assert out["ok"] and out["method"] == "click-title" and out["button"] == "left" and out["at"] == [500, 8]
    assert ("click", "left") in w.calls and w.cursor_at == (10, 20)


def test_right_click_in_the_middle_when_borderless():
    w = FakeWin("click", titled=False)
    out = run(w)
    assert out["method"] == "click-right" and ("click", "right") in w.calls and ("cursor", 960, 540) in w.calls
    assert w.cursor_at == (10, 20)


def test_all_refused_says_what_to_do():
    w = FakeWin(None)
    out = run(w)
    assert not out["ok"] and out["method"] == "refused" and "click the Fortnite window" in out["error"]
    assert out["tried"][-1] == "click-right" and w.cursor_at == (10, 20)


def test_no_click_when_the_setting_is_off():
    w = FakeWin("click")
    out = run(w, allow_click=False)
    assert not out["ok"] and not any(isinstance(c, tuple) and c[0] == "click" for c in w.calls)
    assert "Settings" in out["error"]


def test_cursor_restored_even_if_the_click_breaks():
    class Boom(FakeWin):
        def click(self, button):
            raise RuntimeError("no input")

    w = Boom(None)
    try:
        run(w)
    except RuntimeError:
        pass
    assert w.cursor_at == (10, 20)
