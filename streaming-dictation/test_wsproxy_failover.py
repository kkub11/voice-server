#!/usr/bin/env python3
"""Failover test for wsproxy.py (2026-11-24, list-based).

The failover is an ordered list of (model, server) candidates:
  WS_PROXY_FAILOVER="model@host:port, model@host:port, ..."
The Dictate Real-time model field names the phone's preferred model
and is tried first (entries with that model, in list order), then the
rest of the list. The bigger models live only on the fast remote
server, so they are only used when that server is up.

Checks, all against real sockets (no LocalAI needed):
 1. parse_failover() parses "model@host:port" and "model@host".
 2. failover_candidates() with no WS_PROXY_FAILOVER returns
    DEFAULT_FAILOVER.
 3. order_candidates() puts the field's model entries first.
 4. A session whose first-listed server is down falls through to the
    next candidate, and the upstream sees the right ?model= and the
    session.update rewritten to the chosen model.
 5. A session whose field names a model later in the list tries that
    model first (on its server) even though it is not first.
 6. HTTP GET /v1/models via the proxy reaches the first reachable
    server in the list.
 7. A field naming a later list entry wins over the first entry.
 8. When the field's candidate cannot connect, wsproxy falls back
    to the next list candidate and rewrites session.update.

Run: python3 test_wsproxy_failover.py
"""
import base64
import hashlib
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wsproxy  # noqa: E402
from wsproxy import WS_GUID  # noqa: E402

HOST = "127.0.0.1"
FAST_PORT = 18101    # fast remote (nothing listens unless the test starts it)
MAIN_PORT = 18102    # main local
PROXY_PORT = 18104
# Second scenario uses different ports so a lingering socket from the
# first scenario cannot contaminate it.
FAST_PORT2 = 18111
MAIN_PORT2 = 18112
PROXY_PORT2 = 18114
MAIN_PORT3 = 18122
PROXY_PORT3 = 18124
MAIN_PORT4 = 18132
PROXY_PORT4 = 18134

ok = True


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
    ok = ok and cond


def ws_handshake_response(key):
    accept = base64.b64encode(
        hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    return (f"HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode()


def fake_upstream_server(port, results, stop, reject_model=None):
    """Minimal upstream: WS handshake on /v1/realtime (records the
    ?model= and the first session.update's transcription model), and
    HTTP /v1/models. Accepts in a loop."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, port))
    srv.listen(8)
    srv.settimeout(0.5)
    while not stop.is_set():
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        threading.Thread(target=_handle_one, args=(conn, results, reject_model),
                         daemon=True).start()
    srv.close()


def _handle_one(conn, results, reject_model=None):
    try:
        conn.settimeout(5)
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                return
            data += chunk
        head, rest = data.split(b"\r\n\r\n", 1)
        lines = head.decode("iso-8859-1").split("\r\n")
        method, path, _ = lines[0].split(" ", 2)
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        if headers.get("upgrade", "").lower() == "websocket":
            if reject_model and f"model={reject_model}" in path:
                results["rejected_ws_model"] = path
                conn.sendall(b"HTTP/1.1 500 Internal Server Error\r\n"
                             b"Content-Length: 0\r\n\r\n")
                return
            results["ws_model"] = path
            conn.sendall(ws_handshake_response(headers.get("sec-websocket-key", "")))
            # Read one WS frame (the session.update) to record its model.
            try:
                buf = b""
                while len(buf) < 2:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    buf += chunk
                ln = buf[1] & 0x7F
                if ln == 126:
                    while len(buf) < 4:
                        buf += conn.recv(4096)
                    ln = int.from_bytes(buf[2:4], "big")
                    hdr = 4
                elif ln == 127:
                    while len(buf) < 10:
                        buf += conn.recv(4096)
                    ln = int.from_bytes(buf[2:10], "big")
                    hdr = 10
                else:
                    hdr = 2
                mask = buf[1] & 0x80
                need = hdr + (4 if mask else 0) + ln
                while len(buf) < need:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    buf += chunk
                payload = bytearray(buf[hdr + (4 if mask else 0):need])
                if mask:
                    key = buf[hdr:hdr + 4]
                    for i in range(len(payload)):
                        payload[i] ^= key[i % 4]
                msg = json.loads(bytes(payload).decode("utf-8"))
                tr = msg.get("session", {}).get("audio", {}).get("input", {}).get("transcription", {})
                results["session_update_model"] = tr.get("model")
                results["session_update_masked"] = bool(mask)
            except Exception:
                pass
            time.sleep(0.5)
        else:
            body = json.dumps({"data": [{"id": "fake-model"}]}).encode()
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                         b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def run_proxy(env_extra, proxy_port):
    env = dict(os.environ, WS_PROXY_LISTEN_PORT=str(proxy_port),
               WS_PROXY_FAILOVER_TIMEOUT="0.5")
    env.update(env_extra)
    proc = subprocess.Popen(
        [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "wsproxy.py")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(50):
        try:
            with socket.create_connection((HOST, proxy_port), timeout=0.2):
                return proc
        except OSError:
            time.sleep(0.1)
    proc.terminate()
    raise RuntimeError("proxy never started listening")


def ws_session(field_model, proxy_port):
    """Open a Dictate-style WS session to the proxy, sending a
    session.update with the given field model ('' = blank). Returns
    the raw handshake response."""
    s = socket.create_connection((HOST, proxy_port), timeout=5)
    key = base64.b64encode(b"0123456789abcdef").decode()
    s.sendall(f"GET /v1/realtime?intent=transcription HTTP/1.1\r\n"
              f"Host: {HOST}:{proxy_port}\r\n"
              "Upgrade: websocket\r\n"
              "Connection: Upgrade\r\n"
              f"Sec-WebSocket-Key: {key}\r\n"
              "Sec-WebSocket-Version: 13\r\n\r\n".encode())
    resp = s.recv(4096)
    if not resp.startswith(b"HTTP/1.1 101"):
        s.close()
        return resp, None
    # Send session.update as a masked text frame (client -> proxy).
    msg = {"type": "session.update",
           "session": {"type": "transcription",
                       "audio": {"input": {"transcription": {"model": field_model},
                                           "turn_detection": None}}}}
    payload = json.dumps(msg).encode()
    mask_key = b"\x01\x02\x03\x04"
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    if len(payload) < 126:
        hdr = bytes([0x81, 0x80 | len(payload)]) + mask_key
    else:
        hdr = bytes([0x81, 0x80 | 126]) + len(payload).to_bytes(2, "big") + mask_key
    s.sendall(hdr + masked)
    time.sleep(0.4)
    s.close()
    return resp, s


def main():
    # --- unit: parse / candidates / ordering ---------------------------
    check("parse_failover handles host:port and bare host",
          wsproxy.parse_failover("a@h1:8081, b@h2") ==
          [("a", "h1", 8081), ("b", "h2", 8081)])
    check("parse_failover skips blanks",
          wsproxy.parse_failover("  , a@h1:8081 ,") == [("a", "h1", 8081)])
    try:
        wsproxy.parse_failover("no-at-sign")
        check("parse_failover rejects a bare model", False)
    except ValueError:
        check("parse_failover rejects a bare model", True)

    os.environ.pop("WS_PROXY_FAILOVER", None)
    check("no WS_PROXY_FAILOVER -> DEFAULT_FAILOVER",
          wsproxy.failover_candidates() == wsproxy.parse_failover(wsproxy.DEFAULT_FAILOVER))
    cands = [("whisper", HOST, FAST_PORT), ("nemotron", HOST, FAST_PORT),
             ("eou", HOST, MAIN_PORT)]
    check("order_candidates puts the field's model first",
          wsproxy.order_candidates(cands, "nemotron") ==
          [("nemotron", HOST, FAST_PORT), ("whisper", HOST, FAST_PORT),
           ("eou", HOST, MAIN_PORT)])
    check("order_candidates leaves a blank field alone",
          wsproxy.order_candidates(cands, "") == cands)
    dup = [("whisper", HOST, FAST_PORT), ("tdt", HOST, FAST_PORT),
           ("tdt", HOST, MAIN_PORT), ("eou", HOST, MAIN_PORT)]
    check("order_candidates keeps duplicate-model hosts in list order",
          wsproxy.order_candidates(dup, "tdt") ==
          [("tdt", HOST, FAST_PORT), ("tdt", HOST, MAIN_PORT),
           ("whisper", HOST, FAST_PORT), ("eou", HOST, MAIN_PORT)])

    # --- integration: failover list, fast remote down ------------------
    # Only the MAIN server is up (fast remote port is dead). The list
    # prefers whisper/nemotron on the fast remote, then EOU on main.
    # A session must fall through to EOU on main, and the upstream
    # must see ?model=dictate-streaming and the session.update model
    # rewritten to dictate-streaming (the field named whisper).
    results = {}
    stop = threading.Event()
    t = threading.Thread(target=fake_upstream_server,
                         args=(MAIN_PORT, results, stop), daemon=True)
    t.start()
    time.sleep(0.2)
    proc = run_proxy({
        "WS_PROXY_FAILOVER":
            f"dictate-streaming-whisper@{HOST}:{FAST_PORT},"
            f"dictate-streaming-nemotron@{HOST}:{FAST_PORT},"
            f"dictate-streaming@{HOST}:{MAIN_PORT}",
    }, PROXY_PORT)
    try:
        resp, _ = ws_session("dictate-streaming-whisper", PROXY_PORT)
        check("proxy answers WS handshake with fast remote down",
              resp.startswith(b"HTTP/1.1 101"), f"got {resp[:40]!r}")
        time.sleep(0.3)
        check("fallback upstream got ?model=dictate-streaming",
              results.get("ws_model", "").endswith("?model=dictate-streaming"),
              f"got {results.get('ws_model')!r}")
        check("session.update model rewritten to the fallback model",
              results.get("session_update_model") == "dictate-streaming",
              f"got {results.get('session_update_model')!r}")
        check("buffered session.update is masked (client->server frame)",
              results.get("session_update_masked") is True,
              f"got {results.get('session_update_masked')!r}")
        # HTTP: GET /v1/models via the proxy reaches the main server.
        conn = http.client.HTTPConnection(HOST, PROXY_PORT, timeout=5)
        conn.request("GET", "/v1/models")
        r = conn.getresponse()
        body = json.loads(r.read().decode())
        check("HTTP /v1/models via proxy reaches the live server",
              r.status == 200 and body.get("data", [{}])[0].get("id") == "fake-model",
              f"status={r.status} body={body}")
        conn.close()
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out = ""
        stop.set()

    # --- integration: field's model tried first ------------------------
    # Both servers up now. The list is [whisper@fast, eou@main] but
    # the field names EOU, so EOU on main must win over whisper on fast.
    results2 = {}
    stop2 = threading.Event()
    t2 = threading.Thread(target=fake_upstream_server,
                          args=(FAST_PORT2, results2, stop2), daemon=True)
    t2.start()
    results3 = {}
    stop3 = threading.Event()
    t3 = threading.Thread(target=fake_upstream_server,
                          args=(MAIN_PORT2, results3, stop3), daemon=True)
    t3.start()
    time.sleep(0.2)
    proc = run_proxy({
        "WS_PROXY_FAILOVER":
            f"dictate-streaming-whisper@{HOST}:{FAST_PORT2},"
            f"dictate-streaming@{HOST}:{MAIN_PORT2}",
    }, PROXY_PORT2)
    try:
        resp, _ = ws_session("dictate-streaming", PROXY_PORT2)
        check("proxy answers WS handshake with both servers up",
              resp.startswith(b"HTTP/1.1 101"), f"got {resp[:40]!r}")
        time.sleep(0.3)
        check("field's model (EOU) wins over list-first whisper",
              results3.get("ws_model", "").endswith("?model=dictate-streaming")
              and not results2.get("ws_model"),
              f"fast={results2.get('ws_model')!r} main={results3.get('ws_model')!r}")
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out = ""
        stop2.set()
        stop3.set()

    # --- integration: field names a later list entry ------------------
    # The list starts with EOU, but the Dictate field names TDT, so
    # TDT must be tried first.
    results4 = {}
    stop4 = threading.Event()
    t4 = threading.Thread(target=fake_upstream_server,
                          args=(MAIN_PORT3, results4, stop4), daemon=True)
    t4.start()
    time.sleep(0.2)
    proc = run_proxy({
        "WS_PROXY_FAILOVER":
            f"dictate-streaming@{HOST}:{MAIN_PORT3},"
            f"dictate-streaming-tdt@{HOST}:{MAIN_PORT3}",
    }, PROXY_PORT3)
    try:
        resp, _ = ws_session("dictate-streaming-tdt", PROXY_PORT3)
        check("proxy answers WS handshake with field later in list",
              resp.startswith(b"HTTP/1.1 101"), f"got {resp[:40]!r}")
        time.sleep(0.3)
        check("field later in list wins over first entry",
              results4.get("ws_model", "").endswith("?model=dictate-streaming-tdt")
              and results4.get("session_update_model") == "dictate-streaming-tdt",
              f"got {results4!r}")
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out = ""
        stop4.set()

    # --- integration: fallback to next list candidate -----------------
    # Same setup, but the upstream rejects the field's model at the WS
    # handshake. The proxy must fall back to the next list candidate
    # and rewrite session.update to that fallback model.
    results5 = {}
    stop5 = threading.Event()
    t5 = threading.Thread(target=fake_upstream_server,
                          args=(MAIN_PORT4, results5, stop5,
                                "dictate-streaming-tdt"), daemon=True)
    t5.start()
    time.sleep(0.2)
    proc = run_proxy({
        "WS_PROXY_FAILOVER":
            f"dictate-streaming-tdt@{HOST}:{MAIN_PORT4},"
            f"dictate-streaming@{HOST}:{MAIN_PORT4}",
    }, PROXY_PORT4)
    try:
        resp, _ = ws_session("dictate-streaming-tdt", PROXY_PORT4)
        check("proxy answers WS handshake after list fallback",
              resp.startswith(b"HTTP/1.1 101"), f"got {resp[:40]!r}")
        time.sleep(0.3)
        check("fallback uses next list candidate",
              results5.get("rejected_ws_model", "").endswith("?model=dictate-streaming-tdt")
              and results5.get("ws_model", "").endswith("?model=dictate-streaming")
              and results5.get("session_update_model") == "dictate-streaming",
              f"got {results5!r}")
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out = ""
        stop5.set()

    print("\nALL PASS" if ok else "\nSOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
