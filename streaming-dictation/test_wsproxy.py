#!/usr/bin/env python3
"""Local test for wsproxy.py: fake upstream (LocalAI) + fake Dictate client.

Checks:
 1. session.update reaches upstream WITHOUT the turn_detection key
    (but with everything else intact, incl. transcription model).
 2. Live delta + completed events flow back upstream -> proxy -> client.
 3. HTTP GET /v1/models forwards through the proxy to the upstream.
 4. An audio append frame reaches the upstream unchanged.
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
import wsproxy
from wsproxy import WSConn, WS_GUID, rewrite_session_update  # noqa: E402

UP_PORT = 18081
PROXY_PORT = 18084
HOST = "127.0.0.1"

results = {
    "upstream_session_update": None,
    "upstream_frames": [],
    "client_events": [],
}

SESSION_UPDATE = {
    "type": "session.update",
    "session": {
        "type": "transcription",
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "transcription": {"model": "dictate-streaming", "language": "en"},
                "turn_detection": None,
            }
        },
    },
}


def ws_accept(key):
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()


def read_head(sock):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            return None, None, None
        data += chunk
    head = data.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1")
    lines = head.split("\r\n")
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return lines[0], headers, data


def fake_upstream_client(sock):
    try:
        line, headers, _ = read_head(sock)
        if line is None:
            return
        method, path, _ = line.split(" ", 2)
        if headers.get("upgrade", "").lower() == "websocket":
            sock.sendall(("HTTP/1.1 101 Switching Protocols\r\n"
                          "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Accept: {ws_accept(headers['sec-websocket-key'])}\r\n\r\n").encode())
            ws = WSConn(sock)
            sock.settimeout(10)
            got_update = False
            while True:
                try:
                    opcode, payload = ws.read_frame()
                except (ConnectionError, OSError, socket.timeout):
                    break
                if opcode == 8:
                    break
                if opcode == 9:
                    ws.send_frame(10, payload)
                    continue
                if opcode == 1:
                    results["upstream_frames"].append(payload)
                    try:
                        msg = json.loads(payload.decode())
                    except Exception:
                        continue
                    if msg.get("type") == "session.update" and not got_update:
                        got_update = True
                        results["upstream_session_update"] = msg
                        # LocalAI stamps every event "event_TODO"; the
                        # proxy must rewrite these to unique ids.
                        ws.send_frame(1, json.dumps(
                            {"type": "conversation.item.input_audio_transcription.delta",
                             "delta": "hello ",
                             "event_id": "event_TODO"}).encode())
                        # no event_id on this one: must pass through unchanged
                        ws.send_frame(1, json.dumps(
                            {"type": "conversation.item.input_audio_transcription.completed",
                             "transcript": "hello world"}).encode())
                        # an error Dictate must never see (stop-commit reply)
                        ws.send_frame(1, json.dumps(
                            {"type": "error",
                             "error": {"type": "invalid_request_error",
                                       "code": "not_implemented",
                                       "message": "input_audio_buffer.commit in conjunction with VAD",
                                       "param": "", "event_id": "event_TODO"}}).encode())
                        # a different error, which MUST pass through
                        ws.send_frame(1, json.dumps(
                            {"type": "error",
                             "error": {"type": "invalid_request_error",
                                       "code": "session_update_error",
                                       "message": "boom",
                                       "param": "", "event_id": "event_TODO"}}).encode())
            return
        # plain HTTP
        body = json.dumps({"object": "list",
                           "data": [{"id": "dictate-streaming"}]}).encode()
        sock.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                     + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    except Exception as exc:  # noqa: BLE001
        print("upstream handler error:", exc)
    finally:
        try:
            sock.close()
        except OSError:
            pass


def fake_upstream_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, UP_PORT))
    srv.listen(16)
    while True:
        s, _ = srv.accept()
        threading.Thread(target=fake_upstream_client, args=(s,), daemon=True).start()


def fake_dictate_client():
    sock = socket.create_connection((HOST, PROXY_PORT), timeout=15)
    key = base64.b64encode(b"0123456789abcdef").decode()
    sock.sendall((f"GET /v1/realtime?intent=transcription HTTP/1.1\r\n"
                  f"Host: {HOST}:{PROXY_PORT}\r\n"
                  "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Key: {key}\r\n"
                  "Sec-WebSocket-Version: 13\r\n\r\n").encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        resp += sock.recv(4096)
    assert resp.startswith(b"HTTP/1.1 101"), f"proxy handshake failed: {resp!r}"
    ws = WSConn(sock)
    sock.settimeout(10)
    ws.send_frame(1, json.dumps(SESSION_UPDATE).encode(), mask=True)
    ws.send_frame(1, json.dumps(
        {"type": "input_audio_buffer.append",
         "audio": base64.b64encode(b"\x00\x01" * 2400).decode()}).encode(), mask=True)
    deadline = time.time() + 8
    while time.time() < deadline and len(results["client_events"]) < 3:
        try:
            opcode, payload = ws.read_frame()
        except (ConnectionError, OSError, socket.timeout):
            break
        if opcode == 1:
            results["client_events"].append(json.loads(payload.decode()))
    ws.close(1000)
    sock.close()


def main():
    threading.Thread(target=fake_upstream_server, daemon=True).start()
    env = dict(os.environ, WS_PROXY_LISTEN_PORT=str(PROXY_PORT),
               WS_PROXY_UPSTREAM_HOST=HOST, WS_PROXY_UPSTREAM_PORT=str(UP_PORT))
    proc = subprocess.Popen([sys.executable, os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "wsproxy.py")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        # wait for the proxy to listen
        for _ in range(50):
            try:
                s = socket.create_connection((HOST, PROXY_PORT), timeout=1)
                s.close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            print("FAIL: proxy never started listening")
            return 1

        fake_dictate_client()
        time.sleep(0.5)

        conn = http.client.HTTPConnection(HOST, PROXY_PORT, timeout=10)
        conn.request("GET", "/v1/models")
        resp = conn.getresponse()
        models = json.loads(resp.read().decode())
        conn.close()

        ok = True

        def check(name, cond, detail=""):
            nonlocal ok
            print(("PASS " if cond else "FAIL ") + name + (f" -- {detail}" if detail and not cond else ""))
            ok = ok and cond

        upd = results["upstream_session_update"]
        check("upstream got session.update", upd is not None)
        if upd:
            audio_input = upd["session"]["audio"]["input"]
            check("turn_detection key stripped", "turn_detection" not in audio_input,
                  f"keys present: {sorted(audio_input)}")
            check("transcription model intact",
                  audio_input["transcription"]["model"] == "dictate-streaming")
            check("audio format intact",
                  audio_input["format"] == {"type": "audio/pcm", "rate": 24000})
        appends = [f for f in results["upstream_frames"]
                   if b"input_audio_buffer.append" in f]
        check("audio append forwarded", len(appends) == 1,
              f"appends seen upstream: {len(appends)}")
        deltas = [e for e in results["client_events"]
                  if e.get("type", "").endswith("transcription.delta")]
        completed = [e for e in results["client_events"]
                     if e.get("type", "").endswith("transcription.completed")]
        check("delta reached client", len(deltas) == 1 and deltas[0].get("delta") == "hello ",
              f"events: {results['client_events']}")
        check("delta event_id rewritten unique", len(deltas) == 1
              and deltas[0].get("event_id") == "event_0001",
              f"delta event_id: {deltas[0].get('event_id') if deltas else None}")
        check("completed reached client", len(completed) == 1
              and completed[0].get("transcript") == "hello world")
        check("completed without event_id untouched", len(completed) == 1
              and "event_id" not in completed[0],
              f"completed: {completed}")
        codes = [e.get("error", {}).get("code") for e in results["client_events"]
                 if e.get("type") == "error"]
        check("not_implemented error swallowed", "not_implemented" not in codes,
              f"error codes seen: {codes}")
        check("other errors still pass through", "session_update_error" in codes,
              f"error codes seen: {codes}")
        surviving = [e for e in results["client_events"]
                     if e.get("type") == "error"
                     and e.get("error", {}).get("code") == "session_update_error"]
        check("surviving error event_id rewritten", len(surviving) == 1
              and surviving[0].get("event_id") == "event_0002"
              and surviving[0].get("error", {}).get("event_id") == "event_0002",
              f"surviving error: {surviving}")
        check("HTTP /v1/models proxied", models.get("data", [{}])[0].get("id") == "dictate-streaming",
              f"got: {models}")

        # Blank-model fill (2026-11-24): a session.update whose
        # transcription model is blank/missing gets the default
        # candidate model (the pipeline name) so LocalAI rebuilds and
        # live deltas flow; a non-empty model is the phone's own
        # choice and is untouched by this helper when no chosen model
        # is supplied.
        def _su(model):
            return json.dumps({"type": "session.update", "session": {"type": "transcription",
                "audio": {"input": {"transcription": {"model": model}, "turn_detection": None}}}}).encode()
        _blank = json.loads(rewrite_session_update(_su("")))
        check("blank transcription model filled with default candidate",
              _blank["session"]["audio"]["input"]["transcription"]["model"] == wsproxy.DEFAULT_MODEL,
              f"got: {_blank['session']['audio']['input']['transcription']}")
        _kept = json.loads(rewrite_session_update(_su("dictate-streaming-nemotron")))
        check("non-empty transcription model preserved",
              _kept["session"]["audio"]["input"]["transcription"]["model"] == "dictate-streaming-nemotron")

        print("\nALL PASS" if ok else "\nSOME FAILED")
        return 0 if ok else 1
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=5)
            print("--- proxy log ---")
            print(out)
            for needle in ("UPSTREAM delta: 'hello '",
                           "UPSTREAM completed: 'hello world'",
                           "UPSTREAM error code=session_update_error",
                           "DROP upstream error event code=not_implemented",
                           "UPSTREAM first delta raw: ",
                           "REWRITE event_id: 'event_TODO' -> 'event_0001'"):
                if needle not in out:
                    print(f"FAIL proxy log missing: {needle}")
                    ok = False
            print("proxy event logging: " + ("PASS" if ok else "FAIL"))
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
