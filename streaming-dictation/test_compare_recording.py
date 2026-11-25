#!/usr/bin/env python3
"""Test compare_recording.py end to end with a synthetic log + fake batch server.

Builds a fake wsproxy.log with one session that lost a turn (an empty
completed), a tiny fake WAV, and a fake batch endpoint that returns a
known transcript. Runs compare_recording.py and checks it reports the
lost words.
"""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "compare_recording.py")
BATCH_TEXT = ("hello world this is a test of the realtime decoder "
              "and the missing turn is right here")


class BatchHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        body = json.dumps({"text": BATCH_TEXT}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    tmp = tempfile.mkdtemp(prefix="compare-test-")
    # Fake log: one session, turn 2 lost (empty completed), then the
    # recording saved line. Also an earlier session, to prove the
    # script picks the right one.
    log = (
        "23:40:01 WS upgrade /v1/realtime from client\n"
        "23:40:02 UPSTREAM completed: 'earlier session words'\n"
        "23:40:05 RECORDING saved /x/recordings/session-20261123-234001.wav (100 bytes, 1.0s of audio)\n"
        "23:40:06 WS session ended\n"
        "23:43:59 WS upgrade /v1/realtime from client\n"
        "23:44:02 UPSTREAM delta: 'hello'\n"
        "23:44:05 UPSTREAM completed: 'hello world this is a test'\n"
        "23:44:51 UPSTREAM completed: ''\n"
        "23:45:01 UPSTREAM completed: 'and the missing turn is right here'\n"
        "23:45:02 RECORDING saved /x/recordings/session-20261123-234359.wav (2797440 bytes, 58.3s of audio)\n"
        "23:45:02 WS session ended\n"
    )
    log_path = os.path.join(tmp, "wsproxy.log")
    with open(log_path, "w") as f:
        f.write(log)
    # Tiny valid WAV (1 s of silence, 24 kHz mono PCM16).
    wav_path = os.path.join(tmp, "session-20261123-234359.wav")
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(b"\x00\x00" * 24000)
    # Fake batch server on a free port.
    srv = http.server.HTTPServer(("127.0.0.1", 0), BatchHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        proc = subprocess.run(
            [sys.executable, SCRIPT, wav_path, "--log", log_path,
             "--batch-url", f"http://127.0.0.1:{port}/v1/audio/transcriptions",
             "--api-key", "test-key"],
            capture_output=True, text=True, timeout=60)
    finally:
        srv.shutdown()
    print(proc.stdout)
    if proc.stderr:
        print("STDERR:", proc.stderr, file=sys.stderr)
    ok = True
    for want in [
            "streaming turns: 2 (empty completeds: 1)",
            "hello world this is a test and the missing turn is right here",
            "IN BATCH BUT MISSING FROM STREAMING",
            "of the realtime decoder",
            "=> the realtime decoder dropped those words",
    ]:
        if want not in proc.stdout:
            print(f"FAIL: expected {want!r} in output")
            ok = False
    # The earlier session's words must not leak in.
    if "earlier session words" in proc.stdout:
        print("FAIL: earlier session's text leaked into this session")
        ok = False
    print("ALL PASS" if ok and proc.returncode == 0 else "SOME FAILED")
    return 0 if ok and proc.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
