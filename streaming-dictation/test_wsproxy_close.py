#!/usr/bin/env python3
"""Test the wsproxy close-on-commit shortcut (2026-11-16).

When Dictate stops, it waits out a 1.2 s tail silence even when the
turn already completed seconds earlier. The proxy watches turn state
(a `completed` sets it; the next `speech_started` or delta clears
it) and, on a stop-commit with the turn already completed, closes
the session at once. When the turn is still open it must do nothing
(the tail wait protects the last words).

Three scenarios, each a fresh proxy + fake upstream on its own ports:
 1. close-when-completed: delta + completed, then commit -> the
    client must receive a WS close frame (opcode 8) promptly, and
    the commit must have reached the upstream first.
 2. no-close-when-open: delta only (no completed), then commit -> no
    close frame within 1.5 s.
 3. no-close-when-new-turn: delta + completed, then a delta for a
    new turn, then commit -> no close frame within 1.5 s.
"""
import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wsproxy import WSConn, WS_GUID  # noqa: E402

HOST = "127.0.0.1"

SESSION_UPDATE = {
    "type": "session.update",
    "session": {
        "type": "transcription",
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "transcription": {"model": "dictate-streaming-nemotron",
                                  "language": "en"},
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
            return None, None
        data += chunk
    head = data.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1")
    lines = head.split("\r\n")
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return lines[0], headers


def run_scenario(name, up_port, proxy_port, scenario):
    """Run one scenario. Returns (close_seen, commit_reached_upstream)."""
    upstream_got = {"commit": False}
    stop_upstream = threading.Event()

    def upstream_client(sock):
        try:
            line, headers = read_head(sock)
            if line is None:
                return
            if headers.get("upgrade", "").lower() != "websocket":
                return
            sock.sendall(("HTTP/1.1 101 Switching Protocols\r\n"
                          "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Accept: {ws_accept(headers['sec-websocket-key'])}\r\n\r\n").encode())
            ws = WSConn(sock)
            sock.settimeout(10)
            while not stop_upstream.is_set():
                try:
                    opcode, payload = ws.read_frame()
                except (ConnectionError, OSError, socket.timeout):
                    return
                if opcode == 8:
                    return
                if opcode != 1:
                    continue
                try:
                    msg = json.loads(payload.decode())
                except Exception:
                    continue
                if msg.get("type") == "session.update":
                    ws.send_frame(1, json.dumps(
                        {"type": "conversation.item.input_audio_transcription.delta",
                         "delta": "hello ",
                         "event_id": "event_TODO"}).encode())
                    if scenario in ("close", "new-turn"):
                        ws.send_frame(1, json.dumps(
                            {"type": "conversation.item.input_audio_transcription.completed",
                             "transcript": "hello world"}).encode())
                    if scenario == "new-turn":
                        # a new turn starts: its first delta arrives
                        # BEFORE the commit, so the turn is open again
                        ws.send_frame(1, json.dumps(
                            {"type": "conversation.item.input_audio_transcription.delta",
                             "delta": "more ",
                             "event_id": "event_TODO"}).encode())
                elif msg.get("type") == "input_audio_buffer.commit":
                    upstream_got["commit"] = True
        except Exception as exc:  # noqa: BLE001
            print(f"[{name}] upstream handler error:", exc)
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def upstream_server():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((HOST, up_port))
        srv.listen(16)
        srv.settimeout(0.5)
        while not stop_upstream.is_set():
            try:
                s, _ = srv.accept()
            except socket.timeout:
                continue
            threading.Thread(target=upstream_client, args=(s,),
                             daemon=True).start()
        srv.close()

    threading.Thread(target=upstream_server, daemon=True).start()
    env = dict(os.environ, WS_PROXY_LISTEN_PORT=str(proxy_port),
               WS_PROXY_UPSTREAM_HOST=HOST, WS_PROXY_UPSTREAM_PORT=str(up_port),
               WS_PROXY_MODEL="dictate-streaming-nemotron")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "wsproxy.py")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    close_seen = False
    try:
        for _ in range(50):
            try:
                s = socket.create_connection((HOST, proxy_port), timeout=1)
                s.close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            print(f"FAIL [{name}]: proxy never started listening")
            return False, False

        sock = socket.create_connection((HOST, proxy_port), timeout=15)
        key = base64.b64encode(b"0123456789abcdef").decode()
        sock.sendall((f"GET /v1/realtime?intent=transcription HTTP/1.1\r\n"
                      f"Host: {HOST}:{proxy_port}\r\n"
                      "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Key: {key}\r\n"
                      "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += sock.recv(4096)
        assert resp.startswith(b"HTTP/1.1 101"), f"[{name}] handshake failed: {resp!r}"
        ws = WSConn(sock)
        sock.settimeout(10)
        ws.send_frame(1, json.dumps(SESSION_UPDATE).encode(), mask=True)
        # wait for the upstream events to flow back (delta/completed)
        deadline = time.time() + 5
        got = 0
        while time.time() < deadline and got < 2:
            try:
                opcode, _ = ws.read_frame()
            except (ConnectionError, OSError, socket.timeout):
                break
            if opcode == 1:
                got += 1
        time.sleep(0.3)
        ws.send_frame(1, json.dumps(
            {"type": "input_audio_buffer.commit"}).encode(), mask=True)
        # wait for a close frame (or its absence)
        deadline = time.time() + 2
        sock.settimeout(2)
        while time.time() < deadline:
            try:
                opcode, _ = ws.read_frame()
            except (ConnectionError, OSError, socket.timeout):
                break
            if opcode == 8:
                close_seen = True
                break
        try:
            ws.close(1000)
        except OSError:
            pass
        sock.close()
    finally:
        stop_upstream.set()
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out = ""
        if name == "close" and "CLOSE-ON-COMMIT" not in out:
            print(f"FAIL [{name}]: proxy log missing CLOSE-ON-COMMIT")
            print(out)
            return close_seen, upstream_got["commit"]
    return close_seen, upstream_got["commit"]


def main():
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name
              + (f" -- {detail}" if detail and not cond else ""))
        ok = ok and cond

    close_seen, commit_up = run_scenario("close", 18091, 18094, "close")
    check("close when turn completed", close_seen)
    check("commit still forwarded upstream", commit_up)

    close_seen, _ = run_scenario("open", 18092, 18095, "open")
    check("no close when turn still open", not close_seen)

    close_seen, _ = run_scenario("new-turn", 18093, 18096, "new-turn")
    check("no close when a new turn started", not close_seen)

    print("\nALL PASS" if ok else "\nSOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
