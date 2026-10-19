"""Jev client: request shape, answer parsing, and a local fake API for the HTTP paths."""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from uefn_plugin_playtester import jev


def test_request_has_assets_model_and_questions():
    body = jev.build_request({"frame": "ref-a"}, {"q": {"type": "noul", "instructions": "x `frame`"}}, {"ref-a": b"\xff\xd8jpg"}, "")
    assert body["model"] == jev.DEFAULT_MODEL
    asset = body["assets"]["ref-a"]
    assert asset == {"type": "image", "format": "jpg", "data_base64": base64.b64encode(b"\xff\xd8jpg").decode()}
    assert body["questions"]["q"]["type"] == "noul"


def test_check_questions_rules():
    assert jev.check_questions({}) != ""
    assert "type" in jev.check_questions({"a": {"type": "maybe", "instructions": "x"}})
    assert "criteria" in jev.check_questions({"a": {"type": "choice", "instructions": "x"}})
    assert "2-10" in jev.check_questions({"a": {"type": "score", "instructions": "x", "criteria": ["one"]}})
    assert "no criteria" in jev.check_questions({"a": {"type": "box", "instructions": "x", "criteria": {"a": 1}}})
    assert jev.check_questions({"a": {"type": "box", "instructions": "the mug in `frame`"}}) == ""


def test_answer_parsing():
    answers = {
        "n": {"type": "noul", "noul": 0.93},
        "c": {"type": "choice", "choice": "right", "probabilities": {"left": 0.1, "right": 0.8, "none": 0.1}, "confidence": 0.7},
        "s": {"type": "score", "score": 0.6},
        "b": {"type": "box", "box": {"top": 0.25, "bottom": 0.82, "left": 0.44, "right": 0.84}, "confidence": 0.92},
        "bad": {"type": "box", "box": {"top": 0.9, "bottom": 0.1, "left": 0.2, "right": 0.3}},
    }
    assert jev.noul(answers, "n") == 0.93
    assert jev.choice(answers, "c") == ("right", 0.8)
    assert jev.probabilities(answers, "c")["left"] == 0.1
    assert jev.score(answers, "s") == 0.6
    assert jev.box(answers, "b") == {"top": 0.25, "bottom": 0.82, "left": 0.44, "right": 0.84, "confidence": 0.92}
    assert jev.box(answers, "bad") is None
    assert jev.noul(answers, "missing") is None


def test_box_support_by_model():
    assert jev.supports_box("jev-1.14.0")
    assert jev.supports_box("jev-preview")
    assert not jev.supports_box("jev-1.13.0")
    assert not jev.supports_box("jev-latest")


class _Fake(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen: list = []
    reply: dict = {}

    def log_message(self, *args):  # quiet
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length))
        _Fake.seen.append({"headers": dict(self.headers), "body": body, "path": self.path, "conn": id(self.connection)})
        status, payload = _Fake.reply.get("status", 200), _Fake.reply.get("payload", {})
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_decide_sends_header_and_reuses_connection():
    srv = _server()
    try:
        _Fake.seen.clear()
        _Fake.reply = {"status": 200, "payload": {"model": "jev-1.14.0", "answers": {"q": {"type": "noul", "noul": 0.9}},
                                                   "usage": {"input_tokens": 300, "output_tokens": 9}}}
        c = jev.JevClient(key="k-test", host="127.0.0.1", port=srv.server_address[1], secure=False)
        qs = {"q": {"type": "noul", "instructions": "Is it there in `frame`?"}}
        one = c.decide({"frame": "r1"}, qs, {"r1": b"jpg"}, "jev-1.14.0")
        two = c.decide({"frame": "r2"}, qs, {"r2": b"jpg"}, "jev-1.14.0")
        assert one["ok"] and two["ok"]
        assert one["answers"]["q"]["noul"] == 0.9 and one["usage"]["input_tokens"] == 300
        first = _Fake.seen[0]
        assert first["path"] == jev.PATH
        assert first["headers"]["Authorization"] == "Bearer k-test"
        assert first["headers"][jev.IMAGES_HEADER] == "true"
        assert first["body"]["assets"]["r1"]["type"] == "image"
        assert _Fake.seen[0]["conn"] == _Fake.seen[1]["conn"]  # kept alive
        c.close()
    finally:
        srv.shutdown()


def test_decide_errors_are_plain_messages():
    srv = _server()
    try:
        c = jev.JevClient(key="k-test", host="127.0.0.1", port=srv.server_address[1], secure=False)
        qs = {"q": {"type": "noul", "instructions": "x `frame`"}}
        _Fake.reply = {"status": 400, "payload": {"error": "images are not turned on"}}
        out = c.decide({"frame": "r"}, qs, {"r": b"jpg"})
        assert out["ok"] is False and out["status"] == 400 and "images are not turned on" in out["error"]
        _Fake.reply = {"status": 401, "payload": {"error": "bad key"}}
        out = c.decide({"frame": "r"}, qs, {"r": b"jpg"})
        assert "Settings" in out["error"] and "k-test" not in out["error"]
    finally:
        srv.shutdown()


def test_decide_without_key_says_where_to_put_it(monkeypatch):
    monkeypatch.setattr(jev, "api_key", lambda: "")
    out = jev.JevClient().decide({"frame": "r"}, {"q": {"type": "noul", "instructions": "x"}})
    assert out["ok"] is False and out["needs_key"] and "TypeSafe" in out["error"]


def test_decide_reconnects_after_dropped_connection():
    srv = _server()
    port = srv.server_address[1]
    try:
        _Fake.reply = {"status": 200, "payload": {"answers": {}}}
        c = jev.JevClient(key="k", host="127.0.0.1", port=port, secure=False)
        qs = {"q": {"type": "noul", "instructions": "x"}}
        assert c.decide({}, qs)["ok"]
        c._conn.sock.close()  # the server side went away between steps
        assert c.decide({}, qs)["ok"]
    finally:
        srv.shutdown()
