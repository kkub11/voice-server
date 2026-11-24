#!/usr/bin/env bash
# Benchmark one transcription request against the local Parakeet server.
# Usage: ./test-transcribe.sh path/to/sample.wav [more wavs...]
# Prints wall-clock time per file — compare against each file's duration
# (a 10s clip should come back in ~1-2s on odin if the setup is healthy).
set -euo pipefail

cd "$(dirname "$0")"
# shellcheck disable=SC1091
set -a; [ -f .env ] && . ./.env; set +a
KEY="${SERVER__API_KEY:-}"
URL="http://127.0.0.1:8816/v1/audio/transcriptions"

for f in "$@"; do
  dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f" 2>/dev/null || echo "?")
  echo "== $f (audio duration: ${dur}s)"
  curl -s -w "\n--> HTTP %{http_code}  total %{time_total}s\n" \
    -H "Authorization: Bearer $KEY" \
    -F "file=@$f" \
    -F "response_format=json" \
    "$URL"
  echo
done
