"""One keyboard and one mouse: testers that need their window in front take turns.

Windows sends keys and mouse moves only to the window in front, so two desktop testers can't
press at the same moment. A tester holds the lease while it focuses, looks and presses, and
keeps holding it while nobody else waits, so one tester alone plays exactly as before. When
another tester waits, the holder lets go of every key and hands over at the end of its phase,
then gives the waiter a moment to take it before asking again.

Runs can start in Ducky's window (the tab) and in an agent's tool server (another process), so
the lease is a locked file plus one ``wait-<tester>`` file per waiter, not a thread lock.
Targets that take input without focus (a Godot runtime probe, a browser tab) never ask for it.
"""

from __future__ import annotations

import contextlib
import os
import re
import threading
import time
from typing import Any, Callable, Iterator

WAIT_FRESH_S = 3.0   # a wait file older than this belongs to a tester that died
YIELD_S = 0.5        # after handing over, let waiters take the lease first
POLL_S = 0.02


class Stopped(Exception):
    """The tester was stopped while it waited for its turn."""


def _safe(who: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "", str(who or "").lower())[:48] or "tester"


class _FileLock:
    """A non-blocking exclusive lock on one byte of a file (msvcrt on Windows, flock elsewhere)."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.fh: Any = None

    def try_lock(self) -> bool:
        fh = open(self.path, "a+b")
        try:
            fh.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self.fh = fh
        return True

    def unlock(self) -> None:
        fh, self.fh = self.fh, None
        if fh is None:
            return
        try:
            fh.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            fh.close()


class InputLease:
    def __init__(self, folder: str, *, clock: Callable[[], float] = time.time) -> None:
        self.folder = folder
        os.makedirs(folder, exist_ok=True)
        self.lock_path = os.path.join(folder, "input.lock")
        self.holder_path = os.path.join(folder, "holder")  # who took the lease last
        self.clock = clock
        self._held: dict[str, _FileLock] = {}
        self._gave: dict[str, float] = {}
        self._mu = threading.Lock()

    def _wait_file(self, who: str) -> str:
        return os.path.join(self.folder, f"wait-{_safe(who)}")

    def _mark_waiting(self, who: str) -> None:
        with open(self._wait_file(who), "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))

    def _unmark(self, who: str) -> None:
        with contextlib.suppress(OSError):
            os.remove(self._wait_file(who))

    def waiters(self, who: str = "") -> list[str]:
        """Testers other than ``who`` waiting for the lease now."""
        mine = f"wait-{_safe(who)}" if who else ""
        out = []
        now = self.clock()
        try:
            names = os.listdir(self.folder)
        except OSError:
            return []
        for name in names:
            if not name.startswith("wait-") or name == mine:
                continue
            try:
                if now - os.path.getmtime(os.path.join(self.folder, name)) <= WAIT_FRESH_S:
                    out.append(name[5:])
            except OSError:
                continue
        return out

    def _last_holder(self) -> str:
        try:
            with open(self.holder_path, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return ""

    def holds(self, who: str) -> bool:
        with self._mu:
            return who in self._held

    def waiting(self, who: str) -> bool:
        return os.path.exists(self._wait_file(who)) and not self.holds(who)

    def acquire(self, who: str, stop: threading.Event | None = None) -> None:
        """Wait for the lease (raises Stopped when ``stop`` is set while waiting)."""
        if self.holds(who):
            return
        lock = _FileLock(self.lock_path)
        last_mark = 0.0
        try:
            while True:
                if stop is not None and stop.is_set():
                    raise Stopped()
                now = self.clock()
                if now - last_mark > 0.5:  # keep the wait file fresh while waiting
                    self._mark_waiting(who)
                    last_mark = now
                # Just handed over: let a waiter take it first, but only until someone else has held it
                # (two testers both waiting on each other's courtesy would stall every step).
                courteous = (now - self._gave.get(who, 0.0) < YIELD_S and self._last_holder() == _safe(who)
                             and bool(self.waiters(who)))
                if not courteous and lock.try_lock():
                    with self._mu:
                        self._held[who] = lock
                    with contextlib.suppress(OSError), open(self.holder_path, "w", encoding="utf-8") as fh:
                        fh.write(_safe(who))
                    return
                time.sleep(POLL_S)
        finally:
            self._unmark(who)

    def release(self, who: str) -> None:
        with self._mu:
            lock = self._held.pop(who, None)
        if lock is not None:
            lock.unlock()
            self._gave[who] = self.clock()

    def handover(self, who: str, before: Callable[[], None] | None = None) -> bool:
        """At the end of a phase: when someone waits, let go of every key (``before``) and release."""
        if not self.holds(who) or not self.waiters(who):
            return False
        if before is not None:
            with contextlib.suppress(Exception):
                before()
        self.release(who)
        return True

    @contextlib.contextmanager
    def turn(self, who: str, *, needed: bool = True, stop: threading.Event | None = None,
             before_handover: Callable[[], None] | None = None) -> Iterator[None]:
        """Hold the lease for one phase of a step (focus + look, or focus + press)."""
        if not needed:
            yield
            return
        self.acquire(who, stop)
        try:
            yield
        finally:
            self.handover(who, before_handover)


class NoLease:
    """One tester on its own (and tests): every turn is free."""

    @contextlib.contextmanager
    def turn(self, who: str, **_kw: Any) -> Iterator[None]:
        yield

    def release(self, who: str) -> None:
        pass

    def waiting(self, who: str) -> bool:
        return False
