#!/usr/bin/env python3
"""dictate-wsproxy: tiny proxy between Dictate (Android) and LocalAI on odin.

Why it exists (2026-11-15):
  Dictate's OpenAI realtime client always sends
      "session.update": { "session": { "type": "transcription",
          "audio": { "input": { "turn_detection": null, ... } } } }
  i.e. it turns server-side turn detection OFF and commits turns itself.
  LocalAI only opens its live-transcription stream (the thing that emits
  word-by-word `...transcription.delta` events) inside its server-side
  turn-detection loop (semantic_vad). With turn detection off, audio just
  accumulates until commit and the transcript arrives whole at stop --
  exactly the batch behaviour Kanon already has.

  This proxy sits between them and strips the `"turn_detection": null`
  key from Dictate's session.update, so LocalAI keeps the pipeline's
  own semantic_vad turn detection and the live deltas flow. It also
  drops LocalAI's `not_implemented` error event on the way back: when
  the user stops, Dictate sends a commit, and with server turn
  detection on LocalAI answers that commit with an error event --
  which Dictate treats as fatal and cancels the session over. Dropping
  just that one code lets Dictate wait for the server's own turn
  commit to deliver the final text. It also rewrites LocalAI's
  `event_id` on every upstream event: LocalAI stamps every event with
  the literal placeholder `"event_TODO"`, and if Dictate tracks or
  dedupes events by event_id, everything after the first event would
  be dropped -- which looks exactly like "stream runs, nothing
  displays". The proxy stamps unique `event_0001`, `event_0002`, ...
  ids instead, and logs the full JSON of the first delta event per
  session so the raw event (item_id, event_id, every field) is on
  record. Everything else is forwarded byte-for-byte. It also forwards
  plain HTTP (batch /v1/audio/transcriptions, /v1/models, ...) to
  LocalAI, so Dictate can point its single base URL here and both
  paths work.

  Session audio recording (2026-11-23): every `input_audio_buffer.append`
  Dictate sends is also saved, per session, as one 24 kHz mono PCM16
  WAV in recordings/ next to this file. This is the decisive
  experiment for the LocalAI empty-turn bug: when a turn comes back
  empty, run that session's WAV through the batch endpoint
  (parakeet-api :8816) and compare. If the lost turn's words are in
  the WAV, the audio was good and the realtime decoder dropped it;
  if they are not, the audio itself was bad upstream. The recording
  is passive -- it only reads the append payloads and never touches
  the forwarded frames. Delete old WAVs whenever; each is a few MB
  at most (48 KB/s of audio).

  Close-on-commit (2026-11-16): when Dictate stops, it waits out a
  1.2 s tail silence (its IDLE_MS) for the server's closing answer,
  even when the turn already completed seconds earlier -- the clock
  restarts fresh at every stop, so a pre-stop `completed` does not end
  it. The proxy watches the turn state (a `completed` sets it, the
  next `speech_started` or delta clears it) and, when a commit
  arrives with the turn already completed, closes the session at
  once: Dictate sees the close, ends the wait immediately (PROVIDER
  outcome) and commits the transcript it already has. When the turn
  is still open the proxy does nothing and the tail wait protects the
  last words, as before. The only hole is speaking a word and
  stopping inside one VAD tick (~300 ms) after a completed turn --
  then the new turn's `speech_started` may not have arrived yet and
  the word fragment is lost; in practice a stop lands after a
  finished thought, not mid-word, and the 50 ms delay plus the
  re-check at close time narrow it further.

  Close-on-commit race (fixed 2026-11-19): transcription lags the
  VAD by seconds, so a `completed` can arrive after the next turn
  already started. The old code counted any `completed`, so on
  2026-11-19 turn 1's completed landed after turn 2's
  speech_started, the proxy closed the session at the stop, and
  turn 2's whole sentence was lost. Now a `completed` only counts
  when a turn's audio has ended (speech_stopped / committed) and no
  new turn has started since; a late completed for the previous turn
  is ignored, and the tail wait protects the open turn instead.

  stdlib only -- no pip packages needed. Runs on the host (not Docker),
  so the upstream is LocalAI's host-published port (127.0.0.1:8081).

Usage on odin (from ~/streaming-dictation):
    nohup python3 wsproxy.py > wsproxy.log 2>&1 &
    # Dictate odin-streaming base URL becomes http://192.168.1.137:8084/v1/
    # (transcription model, realtime model and the global Recording switch
    #  stay exactly as they are)

Env overrides: WS_PROXY_LISTEN_PORT (default 8084),
  WS_PROXY_UPSTREAM_HOST (default 127.0.0.1), WS_PROXY_UPSTREAM_PORT
  (default 8081), WS_PROXY_MODEL (default dictate-streaming).
"""

import base64
import hashlib
import http.client
import json
import os
import random
import socket
import struct
import sys
import threading
import time
import wave

LISTEN_HOST = os.environ.get("WS_PROXY_LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("WS_PROXY_LISTEN_PORT", "8084"))
UPSTREAM_HOST = os.environ.get("WS_PROXY_UPSTREAM_HOST", "127.0.0.1")
UPSTREAM_PORT = int(os.environ.get("WS_PROXY_UPSTREAM_PORT", "8081"))
MODEL = os.environ.get("WS_PROXY_MODEL", "dictate-streaming")

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_HTTP_HEAD = 65536
RECORDINGS_DIR = os.environ.get(
    "WS_PROXY_RECORDINGS_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings"))


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


# ---------------------------------------------------------------- websocket

class WSConn:
    """Minimal RFC 6455 connection over an already-handshaken socket."""

    def __init__(self, sock):
        self.sock = sock
        self.buf = bytearray()
        self._frag_opcode = None
        self._frag_parts = []

    def _read_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("socket closed while reading")
            self.buf += chunk
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def read_frame(self):
        """Return (opcode, payload), reassembling fragmented messages.
        Control frames (8/9/10) are returned as they arrive."""
        while True:
            b1, b2 = self._read_exact(2)
            fin = bool(b1 & 0x80)
            opcode = b1 & 0x0F
            masked = bool(b2 & 0x80)
            ln = b2 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._read_exact(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else None
            payload = bytearray(self._read_exact(ln)) if ln else bytearray()
            if mask:
                for i in range(len(payload)):
                    payload[i] ^= mask[i % 4]
            if opcode in (8, 9, 10):
                return opcode, bytes(payload)
            if opcode == 0:  # continuation
                self._frag_parts.append(bytes(payload))
                if fin:
                    full = b"".join(self._frag_parts)
                    op = self._frag_opcode
                    self._frag_parts = []
                    self._frag_opcode = None
                    return op, full
                continue
            if fin:
                return opcode, bytes(payload)
            self._frag_opcode = opcode
            self._frag_parts = [bytes(payload)]

    def send_frame(self, opcode, payload=b"", mask=False):
        header = bytearray([0x80 | opcode])
        ln = len(payload)
        mask_bit = 0x80 if mask else 0
        if ln < 126:
            header.append(mask_bit | ln)
        elif ln < 65536:
            header.append(mask_bit | 126)
            header += struct.pack(">H", ln)
        else:
            header.append(mask_bit | 127)
            header += struct.pack(">Q", ln)
        if mask:
            key = random.randbytes(4)
            header += key
            payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + payload)

    def close(self, code=1000):
        try:
            self.send_frame(8, struct.pack(">H", code))
        except OSError:
            pass
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def rewrite_session_update(payload):
    """Strip turn_detection:null from a session.update so LocalAI keeps
    the pipeline's semantic_vad (the live-delta path). Anything else
    passes through untouched."""
    try:
        msg = json.loads(payload.decode("utf-8"))
    except Exception:
        return payload
    if not isinstance(msg, dict) or msg.get("type") != "session.update":
        return payload
    try:
        audio_input = msg["session"]["audio"]["input"]
    except (KeyError, TypeError):
        return payload
    if isinstance(audio_input, dict) and audio_input.get("turn_detection") is None \
            and "turn_detection" in audio_input:
        del audio_input["turn_detection"]
        log("REWRITE session.update: stripped turn_detection:null "
            "(server keeps semantic_vad)")
        return json.dumps(msg, separators=(",", ":")).encode("utf-8")
    return payload


def log_upstream_event(payload, state=None):
    """Log one LocalAI -> Dictate event (type + the telling field) so a
    mic test shows in wsproxy.log exactly what the server sent: whether
    any live delta ever left the server, what the completed text was,
    and which errors arrived. Delta lines are one per word-chunk --
    verbose during a dictation, and that is the point. The FIRST delta
    of each session is also logged as full raw JSON, so the event's
    item_id, event_id and every field are on record (state carries the
    per-session 'first_delta_logged' flag)."""
    try:
        msg = json.loads(payload.decode("utf-8"))
    except Exception:
        return
    if not isinstance(msg, dict):
        return
    t = msg.get("type", "?")
    # Turn state for the close-on-commit shortcut (2026-11-16): a
    # `transcription.completed` means the server has finalized the
    # current turn -- nothing left to decode -- so a stop-commit that
    # arrives now can close the session at once instead of letting
    # Dictate run out its 1.2 s tail wait. Any new speech (a
    # speech_started, or a delta for the next turn) reopens the turn,
    # and the shortcut is off again until that turn completes.
    #
    # Race fixed 2026-11-19: transcription lags the VAD by seconds,
    # so a `completed` can arrive AFTER the next turn's
    # speech_started -- that completed belongs to the previous turn,
    # and counting it cut off a whole second turn on 2026-11-19
    # (turn 1's completed landed after turn 2 had started; the proxy
    # closed the session at the stop and turn 2's audio was lost).
    # So a completed only counts when a turn's audio has ended
    # (speech_stopped / committed) and no new turn has started since
    # (`awaiting_completed`). A completed with no ended turn behind
    # it is ignored -- the safe failure is Dictate waiting out its
    # tail wait, not a lost turn.
    if state is not None:
        if t.endswith("transcription.completed"):
            if state.get("awaiting_completed"):
                state["turn_completed"] = True
        elif t in ("input_audio_buffer.speech_stopped",
                   "input_audio_buffer.committed"):
            state["awaiting_completed"] = True
        elif t.endswith("transcription.delta") or t == "input_audio_buffer.speech_started":
            state["turn_completed"] = False
            state["awaiting_completed"] = False
    if t.endswith("transcription.delta"):
        if state is not None and not state.get("first_delta_logged"):
            state["first_delta_logged"] = True
            log(f"UPSTREAM first delta raw: {payload.decode('utf-8', 'replace')}")
        log(f"UPSTREAM delta: {msg.get('delta', '')!r}")
    elif t.endswith("transcription.completed"):
        log(f"UPSTREAM completed: {msg.get('transcript', '')!r}")
    elif t == "error":
        err = msg.get("error", {}) if isinstance(msg.get("error"), dict) else {}
        log(f"UPSTREAM error code={err.get('code')} msg={err.get('message', '')!r}")
    elif t in ("session.created", "session.updated",
               "input_audio_buffer.cleared"):
        log(f"UPSTREAM {t}")
    else:
        log(f"UPSTREAM event type={t}")


def rewrite_upstream_event(payload, state=None):
    """Rewrite LocalAI -> Dictate events (return None to drop a frame).
    Two fixes in one place:
    (a) Drop LocalAI's "not_implemented" error event. When the user
    stops, Dictate sends input_audio_buffer.commit; with server-side
    turn detection on, LocalAI answers that commit with an error event
    (code not_implemented) instead of a transcript -- and Dictate
    treats ANY error event as fatal and cancels the session (abrupt
    close, then batch fallback). Swallowing just this one code lets
    Dictate wait for the server's own turn commit, which delivers the
    final text as a normal completed event. Every other error passes
    through untouched.
    (b) Give every event a unique event_id. LocalAI stamps every event
    with the literal placeholder "event_TODO" (see
    core/http/endpoints/openai/realtime_transcription.go); if Dictate
    tracks or dedupes events by event_id, everything after the first
    event is dropped -- which looks exactly like "stream runs, nothing
    displays". Unique ids (event_0001, event_0002, ... per session)
    match the OpenAI shape, where event_id is meant to identify the
    event. The id is rewritten wherever it appears in the envelope:
    top level (delta/completed/session events), and nested inside the
    `error` object, where LocalAI puts it on error events -- an error
    event with no top-level id also gets one added, so every forwarded
    event carries a unique top-level id. Events with no event_id
    anywhere pass through untouched."""
    try:
        msg = json.loads(payload.decode("utf-8"))
    except Exception:
        return payload
    if not isinstance(msg, dict):
        return payload
    if msg.get("type") == "error":
        err = msg.get("error")
        if isinstance(err, dict) and err.get("code") == "not_implemented":
            log("DROP upstream error event code=not_implemented "
                "(Dictate's stop-commit, answered by the server's turn commit)")
            return None
    if state is not None:
        old_top = msg.get("event_id")
        err_obj = msg.get("error") if isinstance(msg.get("error"), dict) else None
        old_nested = err_obj.get("event_id") if err_obj is not None else None
        if old_top is not None or old_nested is not None:
            state["event_seq"] = state.get("event_seq", 0) + 1
            new_id = "event_%04d" % state["event_seq"]
            if old_top is not None:
                msg["event_id"] = new_id
            if err_obj is not None and old_nested is not None:
                err_obj["event_id"] = new_id
                if old_top is None:
                    msg["event_id"] = new_id
            if state["event_seq"] == 1:
                shown_old = old_top if old_top is not None else old_nested
                log(f"REWRITE event_id: {shown_old!r} -> {new_id!r} "
                    "(LocalAI stamps every event 'event_TODO')")
            return json.dumps(msg, separators=(",", ":")).encode("utf-8")
    return payload


# ------------------------------------------------------------------- proxy

def pump(src, dst, name, mask_out, rewrite=None, observe=None,
         send_lock=None):
    """Forward frames src -> dst until either side closes/errors.
    rewrite maps a text payload to a new payload, or None to drop the
    frame. observe sees every text payload (logging only). send_lock,
    when given, serializes all writes to dst (the close-on-commit
    shortcut sends a close frame to the client from another thread,
    and two threads must never interleave bytes on one socket)."""
    def send(opcode, payload):
        if send_lock is not None:
            with send_lock:
                dst.send_frame(opcode, payload, mask=mask_out)
        else:
            dst.send_frame(opcode, payload, mask=mask_out)

    try:
        while True:
            opcode, payload = src.read_frame()
            if opcode == 8:  # close: echo it onward and stop
                try:
                    send(8, payload)
                except OSError:
                    pass
                break
            if opcode == 9:  # ping -> pong back to sender
                try:
                    src.send_frame(10, payload, mask=not mask_out)
                except OSError:
                    pass
                continue
            if opcode == 10:  # pong: ignore
                continue
            if opcode in (1, 2):
                if opcode == 1 and observe is not None:
                    observe(payload)
                if rewrite is not None and opcode == 1:
                    payload = rewrite(payload)
                    if payload is None:
                        continue  # rewrite chose to drop this frame
                send(opcode, payload)
    except (ConnectionError, OSError) as exc:
        log(f"pump {name} ended: {exc}")
    finally:
        for c in (src, dst):
            try:
                c.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def ws_handshake_server(sock, headers):
    key = headers.get("sec-websocket-key", "")
    accept = base64.b64encode(
        hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    sock.sendall(
        ("HTTP/1.1 101 Switching Protocols\r\n"
         "Upgrade: websocket\r\n"
         "Connection: Upgrade\r\n"
         f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
    return WSConn(sock)


def ws_connect_upstream(extra_headers):
    sock = socket.create_connection((UPSTREAM_HOST, UPSTREAM_PORT), timeout=30)
    key = base64.b64encode(random.randbytes(16)).decode()
    req = (f"GET /v1/realtime?model={MODEL} HTTP/1.1\r\n"
           f"Host: {UPSTREAM_HOST}:{UPSTREAM_PORT}\r\n"
           "Upgrade: websocket\r\n"
           "Connection: Upgrade\r\n"
           f"Sec-WebSocket-Key: {key}\r\n"
           "Sec-WebSocket-Version: 13\r\n")
    auth = extra_headers.get("authorization")
    if auth:
        req += f"Authorization: {auth}\r\n"
    req += "User-Agent: dictate-wsproxy/1.0\r\n\r\n"
    sock.sendall(req.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("upstream closed during handshake")
        resp += chunk
        if len(resp) > MAX_HTTP_HEAD:
            raise ConnectionError("upstream handshake too large")
    if not resp.startswith(b"HTTP/1.1 101"):
        raise ConnectionError(f"upstream handshake failed: {resp.splitlines()[0]!r}")
    return WSConn(sock)


def handle_ws(client_sock, headers):
    log("WS upgrade /v1/realtime from client")
    try:
        client = ws_handshake_server(client_sock, headers)
        upstream = ws_connect_upstream(headers)
    except Exception as exc:
        log("WS setup failed:", exc)
        try:
            client_sock.close()
        except OSError:
            pass
        return
    log("WS connected: Dictate <-> LocalAI (model=%s)" % MODEL)
    session_state = {"event_seq": 0, "first_delta_logged": False,
                     "turn_completed": False, "awaiting_completed": False,
                     "close_sent": False}
    # Session audio recording (2026-11-23): accumulate every audio
    # append into one PCM buffer; written to a WAV at session end.
    rec_pcm = bytearray()
    rec_started = time.strftime("%Y%m%d-%H%M%S")
    # Serializes every write to the client socket: the upstream->client
    # pump and the close-on-commit shortcut (below) send on it from
    # different threads.
    client_lock = threading.Lock()

    def close_session_at_once():
        # Runs ~50 ms after Dictate's stop-commit, only when the turn
        # already completed: the commit was forwarded upstream first,
        # and by now the server has nothing left to decode, so closing
        # the socket ends Dictate's 1.2 s tail wait at once (its
        # PROVIDER outcome) instead of letting it run out. If a new
        # turn started in those 50 ms (a delta arrived), the flag was
        # cleared and the close is aborted -- the tail wait then
        # protects the new words, as before.
        if session_state.get("close_sent"):
            return
        if not session_state.get("turn_completed"):
            log("CLOSE-ON-COMMIT aborted: new turn started, "
                "leaving the tail wait to Dictate")
            return
        session_state["close_sent"] = True
        log("CLOSE-ON-COMMIT: turn already completed, closing session "
            "at once (no tail wait)")
        try:
            with client_lock:
                client.send_frame(8, struct.pack(">H", 1000), mask=False)
        except OSError:
            pass
        try:
            upstream.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def client_observe(payload):
        try:
            msg = json.loads(payload.decode("utf-8"))
        except Exception:
            return
        if not isinstance(msg, dict):
            return
        if msg.get("type") == "input_audio_buffer.append":
            # Record the audio for the offline-decode experiment.
            # Passive: a bad payload is skipped, never breaks forwarding.
            audio = msg.get("audio")
            if isinstance(audio, str) and audio:
                try:
                    # extend(), not += : += would rebind the name and
                    # make rec_pcm a local of this function
                    # (UnboundLocalError, silently swallowed).
                    rec_pcm.extend(base64.b64decode(audio, validate=False))
                except Exception:
                    pass
            return
        if msg.get("type") == "input_audio_buffer.commit":
            if session_state.get("turn_completed"):
                # 50 ms lets the commit itself reach the upstream
                # first; the close then ends Dictate's tail wait.
                threading.Timer(0.05, close_session_at_once).start()
            else:
                log("COMMIT with turn still open: leaving the tail "
                    "wait to Dictate (last words still decoding)")

    def upstream_rewrite(payload):
        return rewrite_upstream_event(payload, session_state)

    def upstream_observe(payload):
        log_upstream_event(payload, session_state)

    t1 = threading.Thread(target=pump, args=(client, upstream, "client->upstream",
                                             True, rewrite_session_update,
                                             client_observe), daemon=True)
    t2 = threading.Thread(target=pump, args=(upstream, client, "upstream->client",
                                             False, upstream_rewrite,
                                             upstream_observe, client_lock), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    if rec_pcm:
        try:
            os.makedirs(RECORDINGS_DIR, exist_ok=True)
            path = os.path.join(RECORDINGS_DIR, f"session-{rec_started}.wav")
            with wave.open(path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(bytes(rec_pcm))
            dur = len(rec_pcm) / (24000 * 2)
            log(f"RECORDING saved {path} ({len(rec_pcm)} bytes, {dur:.1f}s of audio)")
        except Exception as exc:
            log(f"RECORDING failed: {exc}")
    log("WS session ended")


def handle_http(client_sock, method, path, headers, leftover):
    if headers.get("expect", "").lower() == "100-continue":
        client_sock.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
    body = bytearray(leftover)
    try:
        clen = int(headers.get("content-length", "0") or "0")
    except ValueError:
        clen = 0
    while len(body) < clen:
        chunk = client_sock.recv(min(65536, clen - len(body)))
        if not chunk:
            break
        body += chunk
    hop = {"host", "connection", "keep-alive", "transfer-encoding",
           "upgrade", "expect", "te", "trailer"}
    fwd = {k: v for k, v in headers.items() if k.lower() not in hop}
    conn = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=120)
    try:
        conn.request(method, path, body=bytes(body) if body else None,
                     headers=fwd)
        resp = conn.getresponse()
        resp_body = resp.read()
    except Exception as exc:
        log(f"HTTP {method} {path} -> upstream error: {exc}")
        client_sock.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n"
                            b"Connection: close\r\n\r\n")
        return
    finally:
        conn.close()
    log(f"HTTP {method} {path} -> {resp.status}")
    out = [f"HTTP/1.1 {resp.status} {resp.reason}\r\n".encode()]
    for k, v in resp.getheaders():
        if k.lower() in ("connection", "transfer-encoding"):
            continue
        out.append(f"{k}: {v}\r\n".encode())
    out.append(f"Content-Length: {len(resp_body)}\r\nConnection: close\r\n\r\n".encode())
    client_sock.sendall(b"".join(out) + resp_body)


def read_http_head(sock):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            return None, None, None, None
        data += chunk
        if len(data) > MAX_HTTP_HEAD:
            raise ValueError("HTTP head too large")
    head, rest = data.split(b"\r\n\r\n", 1)
    lines = head.decode("iso-8859-1").split("\r\n")
    method, path, _version = lines[0].split(" ", 2)
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return method, path, headers, rest


def handle_client(client_sock, addr):
    client_sock.settimeout(3600)
    try:
        method, path, headers, rest = read_http_head(client_sock)
        if method is None:
            return
        is_ws = (headers.get("upgrade", "").lower() == "websocket"
                 and "upgrade" in headers.get("connection", "").lower()
                 and path.startswith("/v1/realtime"))
        if is_ws:
            handle_ws(client_sock, headers)
        else:
            handle_http(client_sock, method, path, headers, rest)
    except Exception as exc:
        log(f"connection from {addr} error: {exc}")
    finally:
        try:
            client_sock.close()
        except OSError:
            pass


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_HOST, LISTEN_PORT))
    srv.listen(32)
    log(f"dictate-wsproxy listening on {LISTEN_HOST}:{LISTEN_PORT} "
        f"-> upstream {UPSTREAM_HOST}:{UPSTREAM_PORT} (model={MODEL})")
    try:
        while True:
            client_sock, addr = srv.accept()
            threading.Thread(target=handle_client, args=(client_sock, addr),
                             daemon=True).start()
    except KeyboardInterrupt:
        log("stopping")
        sys.exit(0)


if __name__ == "__main__":
    main()
