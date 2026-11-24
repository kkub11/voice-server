# Voice server on odin — Parakeet STT, OpenAI-compatible API

Goal: Wispr-Flow-quality dictation from any device on the LAN, served by odin
(Dell OptiPlex 5060 SFF, i7-8700, no discrete GPU, Frigate sharing the box),
with ~1–2 s turnaround on short utterances and no per-minute limits.

## Why this model

- **NVIDIA Parakeet TDT 0.6B v3** (int8, via sherpa-onnx): **6.32% average WER**
  on the Hugging Face Open ASR Leaderboard; the English-only **v2 is 6.05%**.
  Whisper large-v3 is 7.44% on the same leaderboard. This is the "6% WER"
  model — and unlike Whisper it is a transducer, not an autoregressive
  decoder, so it runs many times faster than real-time even on CPU.
- Whisper on this CPU cannot meet the latency target: faster-whisper
  large-v3 int8 is roughly real-time *or slower* on CPUs much faster than
  the i7-8700, so a 10 s utterance would mean a 10 s+ wait. Parakeet int8
  benchmarks at ~20–25× real-time on modern laptop CPUs (4 threads);
  on the 8700 expect less — the benchmark step below measures the real number
  instead of trusting anyone's chart.
- Footprint: ~640 MB model, ~3 GB RAM in use, CPU-only — it does not touch
  the UHD 630 iGPU that Frigate's OpenVINO detector uses. The compose file
  caps it at 4 CPUs / 4 GB so dictation bursts can't starve Frigate.
- Automatic punctuation and capitalization, 25 European languages (v3),
  word-level timestamps, and it doesn't hallucinate over silence the way
  Whisper does.

Server: **parakeet-api** — a maintained wrapper that exposes Parakeet
(sherpa-onnx on Linux) as an OpenAI Whisper-compatible API:
`POST /v1/audio/transcriptions` on port **8816**, optional Bearer API key.

## Install on odin (run these yourself; Muse can't reach odin directly)

Copy this folder to odin first (from your laptop: `scp -r voice-server odin:~/`),
then on odin:

```bash
cd ~/voice-server

# 0. Pre-flight — note RAM and current load with Frigate running
nproc; free -h; uptime; docker stats --no-stream

# 1. Config + API key
cp .env.example .env
sed -i "s/^SERVER__API_KEY=.*/SERVER__API_KEY=$(openssl rand -hex 24)/" .env

# 2. Download the model (~640 MB) into ./models
mkdir -p models
docker compose run --rm api download sherpa --out /app/models

# 3. Start it
docker compose up -d
docker compose logs -f api   # wait until it reports listening on 8816, Ctrl-C
```

## Test + benchmark

Record 3–4 real dictations on your phone (5 s, 10 s, 20 s — read a paragraph,
include some names/jargon you actually say), copy the .wav/.m4a files here,
then:

```bash
chmod +x test-transcribe.sh
./test-transcribe.sh sample-5s.wav sample-10s.wav sample-20s.wav
```

Pass criteria: correct text (eyeball it against what you said) and
`total` ≈ 1–2 s for the 5–10 s clips **while Frigate is running normally**.
If the 20 s clip is slow but short ones are fine, that's expected physics —
dictation clients chunk long speech anyway. If even short clips miss,
paste the logs + this output back to Muse before changing anything.

Quick smoke test without a recording (uses the model's bundled test wav,
if the download kept it):

```bash
find models -name '*.wav' | head -3
```

## Clients

Base URL everywhere: `http://<odin-LAN-IP>:8816/v1`
API key: the one in `.env` (Bearer). Model name: whatever the client asks
for — parakeet-api serves its loaded model.

### Android — keep FUTO for typing, add Dictate for dictation
FUTO Keyboard itself **cannot** point at a remote server: it's offline-only
by design (no internet permission). What it *can* do is import an on-device
whisper.cpp model file — that's the slow/inaccurate path you're replacing.
So instead:

1. Install **Dictate Keyboard (Whisper AI)** (open source, DevEmperor).
   It supports "your own transcription server" + custom API server/proxy.
2. In Dictate settings: provider = own server, URL = odin's base URL above,
   API key = your key.
3. Use Dictate's **floating dictation button** to dictate into any app while
   FUTO stays your typing keyboard (or quick-switch keyboards — Dictate has
   a switch-back key).

### Windows / Linux / macOS desktops
Any dictation app that accepts an OpenAI-compatible endpoint works, e.g.
**Voxis** (Win/Mac/Linux, bring-your-own endpoint) — point it at the same
base URL + key. On Linux, hyprwhspr also accepts a custom host/model/key.

### Programmatic / other tools
Anything that speaks the OpenAI audio API: set
`OPENAI_BASE_URL=http://<odin-LAN-IP>:8816/v1` and the key, model name
is accepted/ignored in favour of the loaded Parakeet model.
`/v1/audio/transcriptions/raw` also accepts raw audio bytes.

## Exposure

LAN-only by default — do **not** port-forward 8816. Away from home, reach it
over Tailscale/WireGuard to odin's LAN IP (or add odin to the tailnet).
The API key is a second layer, not a substitute for keeping it off the WAN.

## What this does not do (yet)

- No Wispr-style AI cleanup (filler-word removal, formatting, per-app tone).
  Parakeet gives you punctuation/caps natively; an LLM cleanup pass in the
  client (Dictate has rewording prompts) or a small local LLM is a later add.
- Non-European languages: Parakeet v3 covers 25 European languages.
  For Japanese etc., the fallback is a second, slower Whisper endpoint
  (e.g. Speaches with large-v3-turbo) — expect >2 s there; add only if needed.
- Streaming partial results: this API transcribes on stop, like Wispr's
  push-to-talk. Perceived latency is the benchmark above, not live captions.
