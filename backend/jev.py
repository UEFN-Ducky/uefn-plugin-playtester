"""Jev (TypeSafe's System One model) with images: frames in, typed answers out.

One HTTPS connection stays open between steps so each step pays for the request, not
a new TLS handshake. Frames ride in the request's ``assets`` map as base64 JPEG under
fresh references; every request with an image or a Box question carries the images
header (docs.typesafe.ai/images). The key is the one saved under Settings → LLMs →
TypeSafe.
"""

from __future__ import annotations

import base64
import http.client
import json
import ssl
import threading
import time
import uuid
from typing import Any

HOST = "api.typesafe.ai"
PATH = "/v1/systemone"
SECRET_KEY = "typesafe"
DEFAULT_MODEL = "jev-1.14.0"
IMAGES_HEADER = "TypeSafe-Danger-Zone-Images"
NO_KEY = "No Jev key: paste your TypeSafe key in Settings → LLMs → TypeSafe, then press Test."
_TYPES = ("noul", "choice", "score", "box")
_RETRY = (429, 529)


def api_key() -> str:
    """The TypeSafe key as saved now (the agents' bridge is its own process)."""
    try:
        from backend.agent.secrets import get_key_fresh

        return (get_key_fresh(SECRET_KEY) or "").strip()
    except Exception:
        pass
    try:
        from backend.agent.secrets import get_key

        return (get_key(SECRET_KEY) or "").strip()
    except Exception:
        return ""


def supports_box(model: str) -> bool:
    """Box questions need Jev 1.14 or later; jev-latest still points at 1.13 until TypeSafe moves it."""
    m = (model or "").strip().lower()
    return not (m.startswith("jev-1.13") or m in ("jev-latest", "jev-1"))


def new_ref(label: str) -> str:
    """A reference that can't occur in question text by accident."""
    return f"{uuid.uuid4().hex[:12]}-{label}"


def build_request(state: Any, questions: dict[str, Any], images: dict[str, bytes], model: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "state": state,
        "questions": questions,
    }
    if images:
        body["assets"] = {
            ref: {"type": "image", "format": "jpg", "data_base64": base64.b64encode(data).decode("ascii")}
            for ref, data in images.items()
        }
    return body


def check_questions(questions: Any) -> str:
    """'' when the questions are well formed, else what is wrong (said to the caller)."""
    if not isinstance(questions, dict) or not questions:
        return "questions must be a non-empty object: {id: {type, instructions, criteria?}}"
    for key, q in questions.items():
        if not isinstance(q, dict):
            return f"question {key} must be an object"
        kind = str(q.get("type") or "").strip().lower()
        if kind not in _TYPES:
            return f"question {key}: type must be noul, choice, score or box"
        if not str(q.get("instructions") or "").strip():
            return f"question {key}: instructions are required"
        crit = q.get("criteria")
        if kind == "choice" and (not isinstance(crit, dict) or not crit):
            return f"question {key}: a choice needs a criteria map of options"
        if kind == "score" and (not isinstance(crit, list) or not 2 <= len(crit) <= 10):
            return f"question {key}: a score needs 2-10 levels"
        if kind == "box" and crit:
            return f"question {key}: a box takes no criteria"
    return ""


def explain(status: int, payload: Any, raw: str = "") -> str:
    detail = ""
    if isinstance(payload, dict):
        detail = str(payload.get("error") or payload.get("message") or payload.get("detail") or "")
    detail = (detail or raw or "").strip()[:300]
    if status == 401 or status == 403:
        return "Jev refused the TypeSafe key: check it in Settings → LLMs → TypeSafe."
    if status == 413:
        return "The frame is too big for Jev: pick a smaller image size in Settings → Play Tester."
    if status in _RETRY:
        return "Jev is busy (rate limit): try again in a moment."
    if status == 400:
        return f"Jev refused the request: {detail or 'bad request'}"
    return f"Jev error {status}: {detail or 'no detail'}"


# --------------------------------------------------------------------------- answers


def noul(answers: dict[str, Any], key: str) -> float | None:
    a = answers.get(key)
    if not isinstance(a, dict):
        return None
    for field in ("noul", "probability", "value"):
        if a.get(field) is not None:
            try:
                return float(a[field])
            except (TypeError, ValueError):
                return None
    return None


def choice(answers: dict[str, Any], key: str) -> tuple[str, float] | None:
    a = answers.get(key)
    if not isinstance(a, dict) or not a.get("choice"):
        return None
    name = str(a["choice"])
    probs = a.get("probabilities") if isinstance(a.get("probabilities"), dict) else {}
    try:
        p = float(probs.get(name, a.get("confidence") or 0))
    except (TypeError, ValueError):
        p = 0.0
    return name, p


def probabilities(answers: dict[str, Any], key: str) -> dict[str, float]:
    a = answers.get(key)
    probs = a.get("probabilities") if isinstance(a, dict) else None
    out: dict[str, float] = {}
    if isinstance(probs, dict):
        for name, value in probs.items():
            try:
                out[str(name)] = round(float(value), 3)
            except (TypeError, ValueError):
                continue
    return out


def score(answers: dict[str, Any], key: str) -> float | None:
    a = answers.get(key)
    if not isinstance(a, dict) or a.get("score") is None:
        return None
    try:
        return float(a["score"])
    except (TypeError, ValueError):
        return None


def box(answers: dict[str, Any], key: str) -> dict[str, float] | None:
    a = answers.get(key)
    edges = a.get("box") if isinstance(a, dict) else None
    if not isinstance(edges, dict):
        return None
    try:
        out = {side: min(1.0, max(0.0, float(edges[side]))) for side in ("left", "right", "top", "bottom")}
    except (KeyError, TypeError, ValueError):
        return None
    if out["right"] <= out["left"] or out["bottom"] <= out["top"]:
        return None
    try:
        out["confidence"] = float(a.get("confidence") or 0)
    except (TypeError, ValueError):
        out["confidence"] = 0.0
    return out


# --------------------------------------------------------------------------- client


class JevClient:
    """Thread-safe Jev caller that keeps its HTTPS connection between steps."""

    def __init__(self, *, key: str = "", host: str = HOST, port: int = 443, secure: bool = True, timeout: float = 20.0) -> None:
        self._key = key
        self._host = host
        self._port = port
        self._secure = secure
        self._timeout = timeout
        self._conn: http.client.HTTPConnection | None = None
        self._lock = threading.Lock()
        self.no_box: set[str] = set()

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def _connection(self) -> http.client.HTTPConnection:
        if self._conn is None:
            if self._secure:
                self._conn = http.client.HTTPSConnection(
                    self._host, self._port, timeout=self._timeout, context=ssl.create_default_context()
                )
            else:
                self._conn = http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)
        return self._conn

    def box_ok(self, model: str) -> bool:
        return supports_box(model) and (model or DEFAULT_MODEL) not in self.no_box

    def decide(
        self,
        state: Any,
        questions: dict[str, Any],
        images: dict[str, bytes] | None = None,
        model: str = "",
    ) -> dict[str, Any]:
        """Ask every question about ``state`` in one call: {ok, answers, model, usage, ms} or {ok: False, error}."""
        problem = check_questions(questions)
        if problem:
            return {"ok": False, "error": problem}
        key = (self._key or api_key()).strip()
        if not key:
            return {"ok": False, "error": NO_KEY, "needs_key": True}
        model = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        images = images or {}
        body = json.dumps(build_request(state, questions, images, model)).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "UEFN-Ducky-PlayTester/1.0",
        }
        if images or any(str(q.get("type")) == "box" for q in questions.values()):
            headers[IMAGES_HEADER] = "true"
        started = time.perf_counter()
        status, raw = 0, b""
        with self._lock:
            for attempt in range(3):
                try:
                    conn = self._connection()
                    conn.request("POST", PATH, body=body, headers=headers)
                    resp = conn.getresponse()
                    raw = resp.read()
                    status = int(resp.status)
                    wait = resp.getheader("Retry-After") or ""
                    if resp.getheader("Connection", "").lower() == "close":
                        self.close()
                except (http.client.HTTPException, OSError) as exc:
                    self.close()
                    if attempt == 0:
                        continue  # a kept-alive connection the server dropped: one fresh try
                    return {"ok": False, "error": f"Could not reach Jev: {type(exc).__name__}", "ms": _ms(started)}
                if status in _RETRY and attempt < 2:
                    try:
                        time.sleep(min(2.0, max(0.25, float(wait or 0.5))))
                    except ValueError:
                        time.sleep(0.5)
                    continue
                break
        text = raw.decode("utf-8", errors="replace")
        try:
            payload = json.loads(text) if text.strip() else {}
        except ValueError:
            payload = {}
        if status >= 400 or not isinstance(payload, dict):
            return {"ok": False, "status": status, "error": explain(status, payload, text), "ms": _ms(started)}
        answers = payload.get("answers") if isinstance(payload.get("answers"), dict) else {}
        return {
            "ok": True,
            "model": payload.get("model") or model,
            "answers": answers,
            "usage": payload.get("usage") if isinstance(payload.get("usage"), dict) else {},
            "ms": _ms(started),
        }


def _ms(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))
