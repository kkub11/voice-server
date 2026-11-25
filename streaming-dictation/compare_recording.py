#!/usr/bin/env python3
"""compare_recording.py -- did the realtime path lose a turn?

Automates the decisive test from 2026-11-23: for one wsproxy session
recording, pull that session's streaming text out of wsproxy.log,
batch-decode the same WAV through parakeet-api (:8816), and show a
word-level diff. If words are in the batch text but missing from the
streaming text, the realtime decoder dropped them -- the audio was
good, the decoder lost it.

Usage (on the main local server, from ~/voice-server/streaming-dictation):
    python3 compare_recording.py recordings/session-20261123-234359.wav
    python3 compare_recording.py session-20261123-234359.wav --log wsproxy.log

How it finds the streaming text: the log has one
"RECORDING saved <path>" line per session, at session end. The script
finds that line, scans backwards to the session's "WS upgrade"
line, and collects every "UPSTREAM completed: '...'" in between
(in order). Empty completeds ('') are counted -- each one is a turn
the realtime path returned nothing for.

The batch call is a plain multipart POST to the parakeet-api
transcription endpoint. The API key is read, in order, from
--api-key, the PARAKEET_API_KEY env var, or the parakeet-api
.env file (SERVER__API_KEY=...). Override the endpoint with
--batch-url, the log with --log. stdlib only -- no pip packages.
"""
import argparse
import ast
import difflib
import json
import os
import re
import sys
import urllib.request
import uuid

DEFAULT_BATCH_URL = "http://127.0.0.1:8816/v1/audio/transcriptions"
COMPLETED_RE = re.compile(r"UPSTREAM completed: (.*)$")
RECORDING_RE = re.compile(r"RECORDING saved (.+?)(?: \(\d+ bytes, [\d.]+s of audio\))?\s*$")


def find_session_lines(log_lines, recording_name):
    """Return the log lines for the session that saved recording_name.

    Finds the "RECORDING saved <path>" line mentioning the file, then
    walks backwards to the session's "WS upgrade" line (or the previous
    session's end, whichever comes first). Returns (session_lines,
    recording_line_index) or (None, None) if not found."""
    rec_idx = None
    for i, line in enumerate(log_lines):
        m = RECORDING_RE.search(line)
        if m and recording_name in m.group(1):
            rec_idx = i
            break
    if rec_idx is None:
        return None, None
    start = 0
    for i in range(rec_idx - 1, -1, -1):
        line = log_lines[i]
        if "WS upgrade /v1/realtime" in line:
            start = i
            break
        if "WS session ended" in line or "RECORDING saved" in line:
            start = i + 1
            break
    return log_lines[start:rec_idx + 1], rec_idx


def streaming_text(session_lines):
    """(turns, empty_count) -- the completed transcripts for a session,
    in order, and how many of them were empty."""
    turns = []
    empty = 0
    for line in session_lines:
        m = COMPLETED_RE.search(line)
        if not m:
            continue
        raw = m.group(1).strip()
        try:
            text = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            text = raw.strip("'\"")
        if not isinstance(text, str):
            text = str(text)
        if text.strip():
            turns.append(text)
        else:
            empty += 1
    return turns, empty


def read_api_key(args):
    if args.api_key:
        return args.api_key
    if os.environ.get("PARAKEET_API_KEY"):
        return os.environ["PARAKEET_API_KEY"]
    # parakeet-api/.env next to the streaming-dictation folder, or the
    # --env path if given.
    candidates = []
    if args.env:
        candidates.append(args.env)
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "..", "parakeet-api", ".env"))
    candidates.append(os.path.join(here, ".env"))
    for path in candidates:
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("SERVER__API_KEY="):
                        key = line.split("=", 1)[1].strip().strip("'\"")
                        if key:
                            return key
        except OSError:
            continue
    return ""


def batch_transcribe(wav_path, batch_url, api_key):
    """POST the WAV to the batch endpoint, return the transcript text."""
    with open(wav_path, "rb") as f:
        wav = f.read()
    boundary = uuid.uuid4().hex
    parts = []
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="file"; '
                 b'filename="recording.wav"\r\n')
    parts.append(b"Content-Type: audio/wav\r\n\r\n")
    parts.append(wav)
    parts.append(f"\r\n--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="response_format"\r\n\r\n')
    parts.append(b"json\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(batch_url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = data.get("text", "")
    if not isinstance(text, str):
        raise ValueError(f"unexpected batch response: {data!r}")
    return text


def word_diff(batch_text, stream_text):
    """Word-level diff. Returns (missing, extra, ratio) where missing
    is the list of batch words absent from the streaming text, extra
    is the list of streaming words absent from the batch text, and
    ratio is difflib's similarity (1.0 = identical)."""
    b_words = batch_text.split()
    s_words = stream_text.split()
    sm = difflib.SequenceMatcher(a=b_words, b=s_words, autojunk=False)
    missing, extra = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("delete", "replace"):
            missing.extend(b_words[i1:i2])
        if tag in ("insert", "replace"):
            extra.extend(s_words[j1:j2])
    return missing, extra, sm.ratio()


def main():
    ap = argparse.ArgumentParser(
        description="Compare one session's streaming text (wsproxy.log) "
                    "with a batch decode of its recording.")
    ap.add_argument("recording",
                    help="recording .wav (path or filename in recordings/)")
    ap.add_argument("--log", default="wsproxy.log",
                    help="wsproxy log to read (default: wsproxy.log)")
    ap.add_argument("--batch-url", default=DEFAULT_BATCH_URL,
                    help=f"batch transcription endpoint (default: {DEFAULT_BATCH_URL})")
    ap.add_argument("--api-key", default="",
                    help="parakeet-api key (default: PARAKEET_API_KEY env or parakeet-api/.env)")
    ap.add_argument("--env", default="",
                    help="path to a .env file holding SERVER__API_KEY")
    args = ap.parse_args()

    wav_path = args.recording
    if not os.path.exists(wav_path):
        here = os.path.dirname(os.path.abspath(__file__))
        cand = os.path.join(here, "recordings", os.path.basename(wav_path))
        if os.path.exists(cand):
            wav_path = cand
        else:
            print(f"recording not found: {args.recording}", file=sys.stderr)
            return 1
    recording_name = os.path.basename(wav_path)

    try:
        with open(args.log, encoding="utf-8", errors="replace") as f:
            log_lines = f.readlines()
    except OSError as exc:
        print(f"cannot read log {args.log}: {exc}", file=sys.stderr)
        return 1
    session_lines, _ = find_session_lines(log_lines, recording_name)
    if session_lines is None:
        print(f"no RECORDING saved line for {recording_name} in {args.log}",
              file=sys.stderr)
        print("Is this the log that session ran under? (wsproxy.log is "
              "overwritten when the proxy restarts.)", file=sys.stderr)
        return 1
    turns, empty = streaming_text(session_lines)
    stream_text = " ".join(turns)

    try:
        batch_text = batch_transcribe(wav_path, args.batch_url,
                                      read_api_key(args))
    except Exception as exc:
        print(f"batch transcription failed: {exc}", file=sys.stderr)
        return 1

    missing, extra, ratio = word_diff(batch_text, stream_text)
    b_words = len(batch_text.split())

    print(f"recording: {recording_name}")
    print(f"streaming turns: {len(turns)} (empty completeds: {empty})")
    print(f"batch words: {b_words}, streaming words: {len(stream_text.split())}")
    print()
    print("--- streaming text (from wsproxy.log) ---")
    print(stream_text or "(nothing)")
    print()
    print("--- batch text (parakeet-api) ---")
    print(batch_text or "(nothing)")
    print()
    if not missing and not extra:
        print(f"IDENTICAL ({ratio:.0%} similar) -- no turn lost in this session.")
    else:
        print(f"similarity: {ratio:.0%}")
        if missing:
            print(f"IN BATCH BUT MISSING FROM STREAMING ({len(missing)} words):")
            print("  " + " ".join(missing))
        if extra:
            print(f"IN STREAMING BUT NOT IN BATCH ({len(extra)} words):")
            print("  " + " ".join(extra))
        if missing and not extra:
            print("=> the realtime decoder dropped those words; the audio was good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
