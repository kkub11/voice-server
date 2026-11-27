#!/usr/bin/env python3
"""Test the session-audio recording in wsproxy.py (2026-11-23).

Starts a fake upstream + the proxy (recordings to a temp dir), sends
a session.update and one audio append from a fake client, closes
cleanly, and checks a valid 24 kHz mono PCM16 WAV was written with
exactly the PCM that was sent.
"""
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_wsproxy import WSConn, read_head, ws_accept  # noqa: E402

HOST = "127.0.0.1"


def fake_upstream(port, stop):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, port))
    srv.listen(1)
    srv.settimeout(10)
    try:
        sock, _ = srv.accept()
    except socket.timeout:
        return
    try:
        line, headers, _ = read_head(sock)
        if line is None or headers.get("upgrade", "").lower() != "websocket":
            return
        sock.sendall(("HTTP/1.1 101 Switching Protocols\r\n"
                      "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {ws_accept(headers['sec-websocket-key'])}\r\n\r\n").encode())
        ws = WSConn(sock)
        sock.settimeout(5)
        ws.send_frame(1, json.dumps(
            {"type": "session.created", "event_id": "event_TODO"}).encode())
        while not stop.is_set():
            try:
                opcode, _ = ws.read_frame()
            except (ConnectionError, OSError, socket.timeout):
                return
            if opcode == 8:
                return
    finally:
        try:
            sock.close()
        except OSError:
            pass
        srv.close()


def main():
    rec_dir = tempfile.mkdtemp(prefix="wsproxy-rec-test-")
    up_port, proxy_port = 18191, 18194
    stop = threading.Event()
    threading.Thread(target=fake_upstream, args=(up_port, stop), daemon=True).start()
    env = dict(os.environ, WS_PROXY_LISTEN_PORT=str(proxy_port),
               WS_PROXY_FAILOVER=f"dictate-streaming@127.0.0.1:{up_port}",
               WS_PROXY_RECORDINGS_DIR=rec_dir)
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "wsproxy.py")],
                            env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    ok = True
    try:
        for _ in range(50):
            try:
                s = socket.create_connection((HOST, proxy_port), timeout=1)
                s.close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            print("FAIL: proxy never started listening")
            return 1
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
        assert resp.startswith(b"HTTP/1.1 101"), f"handshake failed: {resp!r}"
        ws = WSConn(sock)
        sock.settimeout(10)
        ws.send_frame(1, json.dumps({"type": "session.update"}).encode(), mask=True)
        # 0.1 s of 24 kHz mono PCM16: 2400 samples * 2 bytes
        pcm = bytes(range(256)) * 18 + bytes(range(192))  # 4800 bytes
        pcm = pcm[:4800]
        assert len(pcm) == 4800
        ws.send_frame(1, json.dumps(
            {"type": "input_audio_buffer.append",
             "audio": base64.b64encode(pcm).decode()}).encode(), mask=True)
        time.sleep(0.5)
        ws.close(1000)
        sock.close()
        # wait for the proxy to write the WAV (session end)
        deadline = time.time() + 5
        wav = None
        while time.time() < deadline:
            files = [f for f in os.listdir(rec_dir) if f.endswith(".wav")]
            if files:
                wav = os.path.join(rec_dir, files[0])
                break
            time.sleep(0.2)
        if wav is None:
            print("FAIL: no WAV written to", rec_dir)
            ok = False
        else:
            with wave.open(wav, "rb") as wf:
                params = (wf.getnchannels(), wf.getsampwidth(),
                          wf.getframerate(), wf.getnframes())
                data = wf.readframes(wf.getnframes())
            if params != (1, 2, 24000, 2400):
                print(f"FAIL: WAV params {params}, expected (1, 2, 24000, 2400)")
                ok = False
            elif data != pcm:
                print("FAIL: WAV PCM does not match the sent append")
                ok = False
            else:
                print(f"PASS: recording {os.path.basename(wav)} is 24 kHz mono "
                      f"PCM16, 2400 frames, bytes match the sent audio")
    finally:
        stop.set()
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out = ""
        if not ok:
            print("--- proxy log ---")
            print(out)
        shutil.rmtree(rec_dir, ignore_errors=True)
    print("\nALL PASS" if ok else "\nSOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
