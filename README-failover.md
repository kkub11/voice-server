# voice-server

A free, local, open-source alternative to Wispr Flow. Three
self-hosted speech-to-text servers on one box, plus a small proxy
that makes live streaming dictation work with the Dictate keyboard
app on Android. Your audio never leaves your network.

Wispr Flow was great — until the free tier ran out and the shared
server kept timing out. This is the replacement: it runs on a home
server (here, a Dell OptiPlex i7-8700, CPU-only, 14 GB RAM), costs
nothing per minute, and in several ways works better than the cloud
service did — text appears while you speak, and a pause while you
think never ends the dictation.

## Layout

```
voice-server/
├── README.md                  # this file
├── Makefile                   # installer / orchestration (see below)
├── .gitignore
├── parakeet-api/              # batch STT — port 8816
│   ├── docker-compose.yml
│   ├── .env.example           # copy to .env (make env does this)
│   └── test-transcribe.sh
├── streaming-dictation/       # realtime STT — LocalAI :8081 + wsproxy :8084
│   ├── docker-compose.yml
│   ├── docker-compose.gpu.yml # GPU override (fast remote) — use with -f
│   ├── wsproxy.py             # stdlib-only proxy between Dictate and LocalAI
│   ├── models/                # LocalAI model + pipeline YAMLs
│   ├── localai-fix-empty-completed-pnc.patch   # optional, see below
│   └── localai-issue-draft.md # upstream issue text (filed as #13438)
└── speaches/                  # Whisper STT — port 8001 (retired, kept as an option)
```

All three services are Docker Compose projects. `wsproxy.py` is the
only host process (plain Python 3, stdlib only, no pip packages).

## Prerequisites

- Docker + Docker Compose (v2) on the host.
- Python 3 on the host, for `wsproxy.py` (stdlib only).
- `openssl` (optional, used by `make env` to generate the API key).
- A LAN IP for the host — Dictate and the other clients point at it.
  Everything here is **LAN-only by design. Do not port-forward any
  of these ports.** Away from home, reach the host over
  Tailscale/WireGuard.

## Quick start

```bash
make env      # create parakeet-api/.env with a generated API key
make up       # start parakeet-api (:8816) and LocalAI (:8081)
make models   # install the LocalAI gallery models (see note below)
make proxy    # start wsproxy.py on :8084 (default model: dictate-streaming-nemotron)
make status   # show containers + whether wsproxy is running
```

Then point Dictate at the services (see "Dictate setup" below).
`make help` lists every target, including `down`, `logs`,
`proxy-stop`, and the opt-in `up-speaches`.

> The `localai-custom.tar` image tarball (~833 MB) is a transfer
> artifact from building a patched image on another machine. It is
> gitignored. Once the image is loaded (`docker load -i
> localai-custom.tar`) you can delete the file.

## The three services

### 1. parakeet-api — batch transcription, port 8816

Dictate without seeing the text as you speak; hit stop and the whole
recording is transcribed in one batch. Also the automatic fallback:
when a realtime turn fails, Dictate re-transcribes the saved WAV
through this endpoint.

- Model: **NVIDIA Parakeet TDT 0.6B** (int8, via sherpa-onnx).
  Default is v3 (25 European languages, ~6.32% average WER on the
  Hugging Face Open ASR Leaderboard). For English-only dictation v2
  is slightly more accurate (~6.05% WER) — set
  `STT__SHERPA__MODEL_ID=sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8`
  in `parakeet-api/.env` and download v2 into `parakeet-api/models/`
  (see that folder's README notes).
- **Automatic punctuation and capitalization.** This is the only
  path in this repo that punctuates reliably.
- Fast, even on CPU: a 10 s clip comes back in ~1–2 s on the
  reference box. Test it with `parakeet-api/test-transcribe.sh
  sample.wav`.
- API: OpenAI Whisper-compatible `POST /v1/audio/transcriptions`,
  optional Bearer API key. `/v1/audio/transcriptions/raw` also
  accepts raw audio bytes. The model name in the request is
  accepted and ignored in favour of the loaded Parakeet model.

**Pros:** fastest accurate option here; punctuates; the reliable
fallback when streaming fails. **Cons:** no live words — you only
see the text after you stop.

### 2. streaming-dictation — live transcription, LocalAI :8081 + wsproxy :8084

Text appears **as you speak**. A pause while thinking does not end
the dictation — silence is never the send signal.

LocalAI (v4.12.1) runs five pipelines; pick one with
`WS_PROXY_MODEL` when starting the proxy:

| Pipeline | Model | Accuracy | Live words | Punctuation |
|---|---|---|---|---|
| `dictate-streaming-nemotron` **(recommended)** | Nemotron Speech Streaming EN 0.6B | 6.93% WER | per-utterance only | **no — see note** |
| `dictate-streaming` | Parakeet realtime EOU 120M | 9.30% WER | yes | no |
| `dictate-streaming-tdt` | Parakeet TDT 0.6B v3 | 6.34% WER | no (text at pauses) | usually |
| `dictate-streaming-tdt-v2` | Parakeet TDT 0.6B v2 (English-only) | 6.05% WER | no (text at pauses) | usually |
| `dictate-streaming-whisper` | Whisper large-v3 (fast remote GPU only) | 7.44% WER | no (text at pauses) | yes |

WERs are vendor-reported, from different eval setups (EOU and
Nemotron from NVIDIA's streaming-model evals, TDT v2/v3 and Whisper
from the Hugging Face Open ASR Leaderboard average) — roughly
comparable, not strictly apples-to-apples. Nemotron's "live words"
is per-utterance, not word-by-word: it emits text when an utterance
finalizes, which in practice means at pauses, and on the main box's
CPU it
finalizes slower than TDT. EOU is the only true word-by-word one.

**Nemotron punctuation — it does not punctuate in practice.**
NVIDIA documents Nemotron as punctuating natively, and a PnC BERT
model is attached in the pipeline, but the streamed `completed`
text is all-lowercase with no punctuation. Confirmed 2026-11-23:
the NeMo-Speech.cpp C++ `Postprocessor` deliberately skips the
attached PnC model at construction because the Nemotron vocab
self-punctuates (its vocab contains `.`, `?`, `!` tokens), and the
model's own sentence-final punctuation is emitted as
`late_punctuation`, which LocalAI's Go layer never reads. So the
flag, the file, and the YAML are all correct — and the text is
still unpunctuated. Fixing it needs a C++ change and a rebuilt
backend `.so`, not a config tweak. The batch endpoint above is the
punctuating path; the Dictate rewording provider (below) is the
other way to get cleaned-up text.

**TDT punctuation is usually there**, not "works" or "unreliable":
`completed` text is usually punctuated, but the punctuation tokens
only sometimes make it into the finished turn (earlier testing found
it intermittent; recent testing found it working well). The per-turn
text (no live deltas) works as designed.

**Known issue — realtime turn loss (upstream).** During long
multi-turn dictations, a whole turn intermittently returns no
deltas and an empty `completed` (or no `completed` at all), while
surrounding turns work fine. A batch decode of the recorded audio
proves the audio was good — the realtime decoder drops the turn.
Filed upstream as
[mudler/LocalAI#13438](https://github.com/mudler/LocalAI/issues/13438);
the full write-up is in `streaming-dictation/localai-issue-draft.md`.
This repo ships the proxy-side session recorder (`recordings/`
next to `wsproxy.py`) that made that proof possible.

**Correction (2026-11-24): the turn loss is only confirmed on EOU.**
The issue as filed says it "reproduces across all three backends
(Nemotron, EOU, TDT)" — that is not proven. Every turn-loss test
was run from a phone whose Dictate Real-time model field was set to
`dictate-streaming` (EOU), and that field overrides the proxy's
pipeline via LocalAI's session.update rebuild. So the sessions that
looked like Nemotron or TDT tests were actually running EOU. Only
EOU (Parakeet realtime EOU 120M) is confirmed to drop turns.
Nemotron and TDT have not been properly tested for it yet. To test
one properly, set the Dictate field to that pipeline (e.g.
`dictate-streaming-nemotron`) or use the patched wsproxy with a
blank field and `WS_PROXY_MODEL` set to it, then run long
multi-turn dictations and watch for empty `completed` events in
`wsproxy.log`.

**Optional patch.** `streaming-dictation/localai-fix-empty-completed-pnc.patch`
is a small Go patch against LocalAI v4.12.1: it skips the `completed`
event when the backend returns no Final (so Dictate keeps its live
partial text instead of wiping it) and enables automatic punctuation
when a PnC model is attached. It fixes real defects, but in practice
it had no noticeable effect on the two problems above — the observed
empty `completed` came from a non-nil Final with empty Text (the
patch only skips a nil Final), and a lost turn has no deltas to
preserve. Apply it only if you are building a custom image and want
those fixes; the stock `localai/localai:latest` image is the
default in `docker-compose.yml`.

#### wsproxy.py

Streaming dictation only works because of `wsproxy.py`, a small
stdlib-only proxy (no pip packages) that sits between Dictate and
LocalAI on host port **8084**.

What it does:

- Injects `?model=<pipeline>` (from `WS_PROXY_MODEL`) so LocalAI
  uses the chosen pipeline.
- Strips the `"turn_detection": null` key from Dictate's
  `session.update` as it passes through, so LocalAI keeps the
  pipeline's own `semantic_vad` and the live deltas flow.
- Drops LocalAI's `not_implemented` error on the stop-commit, which
  Dictate would otherwise treat as fatal.
- Stamps unique event IDs and **close-on-commit**: a `completed`
  only counts toward closing the session when a turn is actually
  awaiting one, so a late `completed` from a previous turn cannot
  discard the turn you are speaking.
- Records every session's audio to `recordings/session-<timestamp>.wav`
  (24 kHz mono PCM16) next to `wsproxy.py` — passive, it never
  touches the forwarded frames. This is what proved the lost turn's
  audio was good.
- Plain HTTP (batch transcriptions, `/v1/models`) is forwarded to
  LocalAI too, so Dictate can point its single base URL here and
  both paths work.
- **Upstream failover.** wsproxy holds an ordered list of
  (model, server) candidates (`WS_PROXY_FAILOVER`, e.g.
  `dictate-streaming-whisper@<fast-remote-ip>:8081, dictate-streaming@127.0.0.1:8081`).
  Dictate's Real-time model field names the phone's preferred model
  and is tried first (entries with that model, in list order), then
  the rest of the list. In single-server mode the field is tried on
  the main local server first, with `WS_PROXY_MODEL` as the fallback
  if it cannot connect. The bigger models live only on the fast
  remote server, so they are only used when it is up; the main local
  server carries the smaller ones as the fallback. The chosen
  (model, server) is logged per session
  (`WS connected: <model>@<host>:<port>`). See "Second server (fast
  remote, GPU) + failover" below.

Setup — run it on the host, from `~/voice-server/streaming-dictation/`:

```bash
pkill -f wsproxy.py   # kill first if already running
WS_PROXY_MODEL=dictate-streaming-nemotron nohup python3 wsproxy.py > wsproxy.log 2>&1 &
# or: WS_PROXY_MODEL=dictate-streaming     nohup python3 wsproxy.py > wsproxy.log 2>&1 &
# or: WS_PROXY_MODEL=dictate-streaming-tdt nohup python3 wsproxy.py > wsproxy.log 2>&1 &
```

(`make proxy` does the same thing; `make proxy-stop` kills it.)
Then point Dictate's streaming provider base URL at the proxy:

```
http://<main-server-IP>:8084/v1/
```

Debugging: see `wsproxy.log`. Raw recordings are in `recordings/`.

### 3. speaches — Whisper STT, port 8001 (retired, kept as an option)

Speaches (formerly faster-whisper-server) is an OpenAI-compatible
STT **and** TTS server built on faster-whisper. It is in this repo
as an alternative, not as a default: `make up` does not start it;
use `make up-speaches` if you want it.

**Pros:**

- **Multilingual, including Japanese.** Whisper supports 99
  languages; pick the model per request. This is the only service
  here that does Japanese out of the box (see "Japanese support").
- **TTS built in** (Kokoro, Piper) — a full audio server, not
  just STT.
- **Dynamic model management** — models auto-download from
  Hugging Face on first use and unload after a TTL. No manual
  model wrangling.
- Web UI (Gradio) for interactive testing, streaming transcription
  via SSE, and a realtime WebSocket API at `/v1/realtime`.
- Many Whisper sizes to trade speed against accuracy (tiny, base,
  small, medium, large-v3, distil variants).

**Cons:**

- **Slower on CPU than Parakeet.** That is why it was retired
  here: on this CPU-only box the Whisper models are noticeably
  slower than Parakeet TDT for the same job, and the batch path
  above is both faster and more accurate for English. On a GPU
  box the gap narrows, and the multilingual/TTS features may be
  worth it.

## Second server (fast remote, GPU) + failover

A second machine with a GPU (a laptop, a spare box) can run a
second LocalAI with GPU acceleration, using the same repo and the
same commands — `make up-gpu` instead of `make up` (the GPU image
via `streaming-dictation/docker-compose.gpu.yml`). It is the first
entry in wsproxy's failover list; the main local server's own
LocalAI is the last. Both boxes serve the same pipeline names, so
a session that asks for `dictate-streaming` gets it on either box.

"Fast remote" and "main local" are generic names on purpose: other
people's systems have different hostnames. The fast remote is the
second, faster machine; the main local is the box wsproxy runs on.

Why: the same 0.6B models run much faster on a GPU than on the
main box's CPU (this is the main fix for Nemotron feeling slow),
and it unlocks the bigger models — Whisper large-v3 (1.55B,
multilingual incl. Japanese, punctuates natively) is too slow on a
CPU but fast enough on a GPU. Whisper is an offline model, so in a
realtime pipeline it behaves like TDT/Nemotron — text at
pauses/turns, not word-by-word like EOU — and it is UNTESTED in a
realtime pipeline as of 2026-11-24, so verify it transcribes a turn
before relying on it.

How the failover works: wsproxy holds an ordered list of
(model, server) candidates (`WS_PROXY_FAILOVER`, e.g.
`dictate-streaming-whisper@<fast-remote-ip>:8081, dictate-streaming-nemotron@<fast-remote-ip>:8081, dictate-streaming@127.0.0.1:8081`).
Dictate's Real-time model field names the phone's preferred (fast,
best) model and is tried first — entries with that model, in list
order — then the rest of the list. The bigger models live only on
the fast remote, so they are only used when it is up; the main
local server carries the smaller ones (EOU, TDT) as the fallback.
A down fast remote delays the fallback by at most ~2 s per
session. The chosen (model, server) is logged per session
(`WS connected: <model>@<host>:<port>`). If the fast remote goes
down mid-session, that session breaks and Dictate falls back to
batch — the next session picks the live box again.

Setup (on the fast remote): `make up-gpu`, then `make models`
(first run) and `make models-gpu` for Whisper. The GPU
prerequisites: the NVIDIA Container Toolkit installed so Docker
can see the GPU (`docker exec localai nvidia-smi` should list it),
and a static IP or DHCP reservation for the fast remote, so the
failover list's address does not move. Wiring up the failover (on
the main local server): `make proxy-failover` (or restart wsproxy
with `WS_PROXY_FAILOVER="<list>"`). Set `FAST_REMOTE_HOST` in the
Makefile (or `make proxy-failover FAST_REMOTE_HOST=<ip>`) if the
fast remote's IP differs from the default.

## Alternatives not implemented here

This repo is local-first and free. The options below were
considered and not built in, but other users should know they
exist — especially if they want the one thing no local/free model
here does: word-by-word streaming **and** punctuation **and** low
WER in a single model.

**Cloud streaming STT (paid, does it all).** Deepgram Nova-3 is
the closest to "does it all": true word-by-word streaming,
punctuates and capitalizes natively, low WER, very fast. It is a
cloud API, not local, and it is paid (~$0.006/min, so heavy daily
use is a few dollars a month; there is a free-credit tier to try
it). AssemblyAI (Universal-Streaming) and Soniox are similar
freemium streaming services. If you are willing to pay a little
and send audio to a cloud, one of these replaces the whole
streaming stack. OpenAI's Realtime API (gpt-4o-realtime) also
streams, but it is the most expensive of the bunch.

**Cloud batch STT (free tier, no streaming).** Groq offers
Whisper large-v3 via its API on a free tier, and it is very fast
(Groq's LPU). But it is a `POST /v1/audio/transcriptions` call,
not a live stream — it can only replace the batch provider
(:8816), not the live-words path. Gemini and Hugging Face
Inference offer similar batch transcription. None of the free
cloud tiers offer true word-by-word realtime streaming; that is
a paid feature everywhere.

**OmniRoute (the chat gateway in this stack).** OmniRoute routes
OpenAI-compatible HTTP calls (chat/completions) to free/cheap
cloud models. It is not an STT gateway: it does not proxy the
realtime WebSocket protocol, and the free cloud providers behind
it do not offer realtime streaming STT. It *could* route a batch
transcription call (e.g. to Groq Whisper) as an alternative batch
provider, but this repo already has a good local one (:8816), so
there is no reason to. The main-local/fast-remote failover is done
directly in wsproxy, not via OmniRoute, for the same reason.

## Dictate setup (Android)

Add the services as providers in Dictate's Settings, and use
Dictate as the keyboard on your phone. A Wispr Flow-like floating
button is available too.

- **Batch provider** ("main"): custom server, base URL
  `http://<main-server-IP>:8816/v1`, API key from
  `parakeet-api/.env`. ("Test connection" returning
  `{"detail":"Not Found"}` is a red herring; "Test transcription"
  is the real check.)
- **Streaming provider** ("main-streaming"): custom server, base
  URL `http://<main-server-IP>:8084/v1/`, real-time streaming ON,
  real-time model `dictate-streaming-nemotron` (your preferred
  pipeline; wsproxy tries it first and falls back to
  `WS_PROXY_MODEL` or the failover list if it cannot connect). Live
  word-by-word text only appears when Dictate itself is the active
  keyboard — with the floating button over another keyboard, live
  text is deliberately held back (Dictate issue #456).
- **Rewording provider** (for the "Fix Grammar" etc. buttons):
  add a *separate* custom provider pointing at an OpenAI-compatible
  **chat** endpoint — here, OmniRoute on the same host:
  base URL `http://<main-server-IP>:20128/v1`, model `general`, your
  OmniRoute client key. `general` is a free chain (Gemini 3 Flash
  Preview → Groq GPT-OSS 120B → NVIDIA GPT-OSS 20B → Kiro
  DeepSeek 3.2 → Z.ai GLM 4.7 Flash → Cloudflare Llama 3.3 70B →
  Gemini 3.1 Flash Lite → Nemotron Ultra). Then select it in
  Settings → Rewording and make sure Rewording is enabled. The
  buttons work by queuing a rewrite that is applied when you hit
  stop. Note: the "Test transcription" button on this provider
  will fail — `general` is a chat combo and cannot transcribe
  audio. That is expected; tap OK to save it anyway and use it
  only for rewording, never as the transcription provider.

## Other clients

Anything that speaks the OpenAI audio API works against the batch
endpoint — set `OPENAI_BASE_URL=http://<main-server-IP>:8816/v1` and
the key. Desktop dictation apps that accept a custom
OpenAI-compatible endpoint include **Voxis** (Windows/Mac/Linux)
and, on Linux, **hyprwhspr** — point them at the same base URL +
key. On the programmatic side the same base URL works with any
OpenAI SDK.

## Exposure

LAN-only by default — **do not port-forward 8816, 8081, or 8084**.
Away from home, reach the host over Tailscale/WireGuard to the
host's LAN IP. The API key is a second layer, not a substitute for
keeping the services off the WAN. The plain-HTTP warning Dictate
shows for these providers is fine on your own network.

## Japanese support

Short version: **batch Japanese works today; live Japanese
streaming does not.**

- **Batch (works):** point a client at **speaches** (`:8001`) and
  request a Whisper model — Whisper is multilingual and handles
  Japanese. Alternatively, use a multilingual sherpa-onnx model
  with parakeet-api (the v3 model covers 25 European languages,
  so for Japanese you would need a different sherpa-onnx model
  ID, e.g. a Whisper-based one, in `parakeet-api/.env`).
- **Streaming (open gap):** none of the current LocalAI pipelines
  support Japanese. Parakeet TDT v2 is English-only; TDT v3 covers
  25 European languages; Nemotron streaming is English-only; the
  EOU 120M model is English-only. A Japanese streaming pipeline
  would need a Japanese-capable streaming ASR model in LocalAI
  (a Japanese Parakeet or a streaming Whisper variant) plus a
  matching pipeline YAML — not built here yet.
- **Rewording in Japanese:** the OmniRoute `general` chain above
  handles Japanese text fine — the gap is transcription, not
  the rewording step.

## Troubleshooting

- `docker ps` / `make status` — are `parakeet-api` and `localai`
  up, and is `wsproxy.py` running?
- `make logs` — follow both containers' logs.
- `streaming-dictation/wsproxy.log` — every upstream event is
  logged; `UPSTREAM completed:` lines show exactly what LocalAI
  returned per turn.
- `streaming-dictation/recordings/` — the recorded WAV for a
  session; play it back or batch-decode it through `:8816` to
  check whether the audio was good when a turn came back empty.
- A stale `wsproxy.py` holding :8084 makes a fresh launch exit
  instantly — `pkill -f wsproxy.py` first (`make proxy-stop`).
- If `docker compose up -d` warns about an orphan `dictate-proxy`
  container (an old pre-wsproxy nginx proxy on :8083), it is safe
  to remove with `docker compose up -d --remove-orphans` in
  `streaming-dictation/`.
