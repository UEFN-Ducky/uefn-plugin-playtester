"""Which brain answers Play Tester's frame questions.

- ``typesafe``: Jev straight at TypeSafe with the key under Settings → LLMs (fastest).
- ``ducky_ai``: the same Jev through Ducky AI (the site's Jev gateway), billed to the plan.
- ``gateway``: any vision model on an installed gateway, asked for the answers as JSON.
  It has no Box, so finding things falls back to left / center / right.
- ``auto`` (default): the TypeSafe key when saved, else Ducky AI when signed in, else the
  gateway (Play Tester's own pick, or the app's default model).

``Brain`` has the same calls as ``jev.JevClient`` (decide, box_ok, no_box, close), so the
pilot and tools don't care which one answers.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import jev

BRAINS = ("auto", "typesafe", "ducky_ai", "gateway")
DUCKY_AI_PATH = "/api/v1/plugins/uefn-ducky/collect/jev-decide"
DUCKY_AI_PROVIDER = "uefn_ducky"
# ponytail: a refused Ducky AI (no plan, used up, not set up) is skipped this long under Auto.
REFUSED_S = 300.0
NO_BRAIN = ("No brain for Play Tester: save a TypeSafe key (Settings → LLMs → TypeSafe), sign in to "
            "Ducky AI, or pick a gateway model in Settings → Play Tester.")


def signed_in() -> bool:
    """A Ducky account is signed in on this PC (Ducky AI may answer; the site decides)."""
    try:
        from frontend.duckyos_account import account_key

        return bool(account_key())
    except Exception:
        return False


def app_model() -> tuple[str, str]:
    """(provider, model) the app's chats default to; ('', '') when unknown."""
    try:
        from frontend.settings import PanelSettings

        s = PanelSettings.load()
        qualified = str(s.default_model or "")
        if ":" in qualified:
            provider, model = (part.strip() for part in qualified.split(":", 1))
        else:
            provider, model = str(s.agent_provider or "").strip(), str(s.agent_model or "").strip()
    except Exception:
        return "", ""
    return provider.lower(), model


def gateway_target(settings: dict[str, Any]) -> tuple[str, str]:
    """(provider, model) for the gateway brain: Play Tester's pick, else the app's default model.
    Ducky AI's chat model is skipped: its frames go to Jev instead."""
    provider = str(settings.get("gateway") or "").strip().lower()
    model = str(settings.get("gateway_model") or "").strip()
    if provider and model:
        return provider, model
    provider, model = app_model()
    if provider == DUCKY_AI_PROVIDER or not (provider and model):
        return "", ""
    return provider.lower(), model


def pick(choice: str, has_key: bool, ducky_ok: bool, has_gateway: bool, ducky_selected: bool = False) -> str:
    """The brain that answers now, or '' when none can. Pure. Auto follows Ducky AI first when
    it is the app's model (Jev runs through Ducky AI then, even with a TypeSafe key saved)."""
    choice = choice if choice in BRAINS else "auto"
    if choice != "auto":
        return choice
    if ducky_selected and ducky_ok:
        return "ducky_ai"
    if has_key:
        return "typesafe"
    if ducky_ok:
        return "ducky_ai"
    return "gateway" if has_gateway else ""


# --------------------------------------------------------------------------- Ducky AI


def ducky_decide(state: Any, questions: dict[str, Any], images: dict[str, bytes], model: str,
                 request: Callable[..., tuple[int, Any, str]] | None = None) -> dict[str, Any]:
    """Jev through Ducky AI: {ok, answers, model, usage, ms} or {ok: False, error, refused?}."""
    started = time.perf_counter()
    if request is None:
        try:
            from frontend.duckyos_account import api_request as request
        except Exception:
            return {"ok": False, "error": "This UEFN Ducky can't reach Ducky AI: update the app.", "refused": True}
    body = jev.build_request(state, questions, images, model)
    try:
        status, parsed, raw = request("POST", DUCKY_AI_PATH, body, timeout=30.0)
    except Exception as exc:
        msg = getattr(exc, "message", None) or str(exc) or "the website did not answer"
        return {"ok": False, "error": f"Ducky AI: {msg}"[:300], "ms": jev._ms(started)}
    payload = parsed if isinstance(parsed, dict) else {}
    payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
    if status != 200 or not isinstance(payload.get("answers"), dict):
        why = str(payload.get("error") or raw or f"HTTP {status}").strip()[:300]
        if status == 404:
            why = "this website has no Ducky AI Jev yet"
        # 4xx (no plan, used up, not set up, signed out) won't fix itself in a second.
        return {"ok": False, "error": f"Ducky AI: {why}", "refused": 400 <= status < 500 and status != 429,
                "status": status, "ms": jev._ms(started)}
    return {"ok": True, "model": payload.get("model") or model, "answers": payload["answers"],
            "usage": payload.get("usage") if isinstance(payload.get("usage"), dict) else {}, "ms": jev._ms(started),
            "brain": "ducky_ai"}


# --------------------------------------------------------------------------- gateway

_JSON = re.compile(r"\{.*\}", re.S)


def gateway_prompt(state: Any, questions: dict[str, Any], refs: list[str]) -> str:
    """The questions as one prompt asking for JSON answers. Box questions are left out. Pure."""
    rows = []
    for key, q in questions.items():
        kind = str(q.get("type") or "")
        if kind == "box":
            continue
        line = f'- "{key}" ({kind}): {q.get("instructions")}'
        if kind == "noul":
            line += ' Answer {"noul": probability 0..1 that it is true}.'
        elif kind == "choice":
            opts = q.get("criteria") or {}
            line += f' Answer {{"choice": one of {json.dumps(list(opts))}, "confidence": 0..1}}. Options: {json.dumps(opts)}'
        elif kind == "score":
            line += f' Answer {{"score": 0..1}} on this scale, lowest first: {json.dumps(q.get("criteria") or [])}.'
        rows.append(line)
    pictures = ", ".join(f"picture {i + 1} is `{ref}`" for i, ref in enumerate(refs)) or "no pictures"
    return (
        "You judge frames from a Fortnite game for an automated play tester. Look at the pictures "
        f"({pictures}) and answer every question. Reply with one JSON object only, keyed by question id.\n\n"
        f"State: {json.dumps(state)}\n\nQuestions:\n" + "\n".join(rows)
    )


def _unit(value: Any) -> float | None:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return None


def parse_gateway_answers(text: str, questions: dict[str, Any]) -> dict[str, Any]:
    """A model's JSON reply → Jev-shaped answers (unknown or broken ones dropped). Pure."""
    m = _JSON.search(text or "")
    try:
        raw = json.loads(m.group(0)) if m else {}
    except ValueError:
        raw = {}
    out: dict[str, Any] = {}
    for key, q in questions.items():
        a = raw.get(key) if isinstance(raw, dict) else None
        kind = str(q.get("type") or "")
        if not isinstance(a, dict) and kind in ("noul", "score"):
            a = {kind: a}
        if not isinstance(a, dict):
            continue
        if kind == "noul" and _unit(a.get("noul", a.get("probability"))) is not None:
            out[key] = {"type": "noul", "noul": _unit(a.get("noul", a.get("probability")))}
        elif kind == "choice" and str(a.get("choice") or "") in (q.get("criteria") or {}):
            conf = _unit(a.get("confidence"))
            conf = 0.7 if conf is None else conf
            out[key] = {"type": "choice", "choice": str(a["choice"]), "probabilities": {str(a["choice"]): conf},
                        "confidence": conf}
        elif kind == "score" and _unit(a.get("score")) is not None:
            out[key] = {"type": "score", "score": _unit(a.get("score"))}
    return out


def gateway_decide(state: Any, questions: dict[str, Any], images: dict[str, bytes], provider: str, model: str,
                   complete: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    """One vision-model call through an installed gateway, answers in Jev's shape."""
    started = time.perf_counter()
    if not (provider and model):
        return {"ok": False, "error": NO_BRAIN, "needs_key": True}
    if complete is None:
        try:
            from backend.automations.llm_complete import complete_prompt as complete
        except Exception:
            return {"ok": False, "error": "This UEFN Ducky can't ask a gateway model from a plugin: update the app."}
    # ponytail: complete_prompt takes picture paths, so frames go through a temp folder per call.
    folder = Path(tempfile.mkdtemp(prefix="playtester-"))
    try:
        paths = []
        for ref, data in images.items():
            path = folder / f"{ref}.jpg"
            path.write_bytes(data)
            paths.append(str(path))
        try:
            res = complete(provider, gateway_prompt(state, questions, list(images)), model,
                           system="Reply with JSON only.", images=paths)
        except Exception as exc:
            res = {"ok": False, "error": str(exc)}
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if not res.get("ok"):
        return {"ok": False, "error": f"{provider}: {res.get('error') or 'no answer'}"[:300], "ms": jev._ms(started)}
    answers = parse_gateway_answers(str(res.get("text") or ""), questions)
    if not answers:
        return {"ok": False, "error": f"{model} gave no usable answers (it may not see pictures).", "ms": jev._ms(started)}
    return {"ok": True, "model": f"{provider}:{model}", "answers": answers, "usage": {}, "ms": jev._ms(started),
            "brain": "gateway"}


# --------------------------------------------------------------------------- brain


class Brain:
    """Routes each decide to the brain the settings pick; same calls as ``jev.JevClient``."""

    def __init__(self, client: jev.JevClient | None = None, settings: Callable[[], dict[str, Any]] | None = None,
                 *, ducky: Callable[..., dict[str, Any]] | None = None, gateway: Callable[..., dict[str, Any]] | None = None,
                 signed: Callable[[], bool] | None = None, key: Callable[[], str] | None = None,
                 model: Callable[[], tuple[str, str]] | None = None) -> None:
        self.client = client or jev.JevClient()
        self._settings = settings or (lambda: {})
        self._ducky = ducky or ducky_decide
        self._gateway = gateway or gateway_decide
        self._signed = signed or signed_in
        self._key = key or jev.api_key
        self._app_model = model or app_model
        self._refused_at = float("-inf")
        self._lock = threading.Lock()

    @property
    def no_box(self) -> set[str]:
        return self.client.no_box

    def close(self) -> None:
        self.client.close()

    def choose(self) -> dict[str, Any]:
        """{brain, choice, gateway: (provider, model)}: who answers the next question."""
        s = self._settings() or {}
        choice = str(s.get("brain") or "auto").strip().lower()
        with self._lock:
            refused = time.monotonic() - self._refused_at < REFUSED_S
        target = gateway_target(s)
        ducky_selected = self._app_model()[0] == DUCKY_AI_PROVIDER
        brain = pick(choice, bool((self._key() or "").strip()), self._signed() and not refused, bool(target[0]),
                     ducky_selected)
        return {"brain": brain, "choice": choice if choice in BRAINS else "auto", "gateway": target}

    def ready(self) -> dict[str, Any]:
        c = self.choose()
        if not c["brain"] or (c["brain"] == "typesafe" and not (self._key() or "").strip()):
            return {"ok": False, "error": jev.NO_KEY if c["brain"] == "typesafe" else NO_BRAIN, "needs_key": True, **c}
        if c["brain"] == "gateway" and not c["gateway"][0]:
            return {"ok": False, "error": NO_BRAIN, "needs_key": True, **c}
        if c["brain"] == "ducky_ai" and not self._signed():
            return {"ok": False, "error": "Sign in to your Ducky account to use Ducky AI for Play Tester.", "needs_key": True, **c}
        return {"ok": True, **c}

    def box_ok(self, model: str) -> bool:
        return self.choose()["brain"] != "gateway" and self.client.box_ok(model)

    def decide(self, state: Any, questions: dict[str, Any], images: dict[str, bytes] | None = None,
               model: str = "") -> dict[str, Any]:
        problem = jev.check_questions(questions)
        if problem:
            return {"ok": False, "error": problem}
        images = images or {}
        c = self.choose()
        if c["brain"] == "typesafe":
            return {**self.client.decide(state, questions, images, model), "brain": "typesafe"}
        if c["brain"] == "ducky_ai":
            res = self._ducky(state, questions, images, model or jev.DEFAULT_MODEL)
            if res.get("ok") or c["choice"] != "auto":
                return res
            if res.get("refused"):
                with self._lock:
                    self._refused_at = time.monotonic()
            if not c["gateway"][0]:
                return res
            c["brain"] = "gateway"  # Auto: Ducky AI said no, the gateway answers this frame
        if c["brain"] == "gateway":
            qs = {k: q for k, q in questions.items() if str(q.get("type")) != "box"}
            if not qs:
                return {"ok": False, "error": "A gateway model can't draw boxes: use Jev for this question."}
            return self._gateway(state, qs, images, *c["gateway"])
        return {"ok": False, "error": NO_BRAIN, "needs_key": True}

