# voice-server

Your own self-hosted STT (Speech-to-Text) server

## Inspiration

Speaking is faster than typing, especially on a smartphone, but dang, it's frustrating when it makes mistakes. Then Wispr Flow came along, and wow, it was fast AND accurate. But then... I hit the free usage limit and had to go back to inferior Gboard for a while. Wispr Flow would also time out sometimes. (Maybe it was a victim of its own success and the resultant overloaded server?)

This is why I decided to make my own free, private, local, and open-source version, and in many ways, it's even better than Wispr Flow. Sadly, I couldn't replicate it perfectly, and this repo, in current form, is still experimental and unfinished. However, I do use it as my daily-driver. I uninstalled Wispr Flow and haven't looked back.

## Features

- Fast, accurate voice transcription on your phone.
- Live-streaming text. See the text as you speak. Edit it on the fly.
- Your own server: less downtime (we hope).
- Private. Nothing leaves your local network.

## Prerequisites

- Docker + Docker Compose (v2)
- Python 3
- `openssl` (optional, used by `make env` to generate the API key).
- A LAN IP for the host

## Quick start

### Step 1 - Setup the host (your local server or VPS)

Run these in order on the server:

```bash
make env      # create parakeet-api/.env with a generated API key
make up       # start parakeet-api (:8816) and LocalAI (:8081)
make models   # install the LocalAI gallery models (see note below)
make proxy    # start wsproxy.py on :8084 (default model: dictate-streaming-nemotron)
make status   # show containers + whether wsproxy is running
```

(Note: Use `make help` for the full list of commands, including `down`, `logs`, `proxy-stop`, and the opt-in `up-speaches`.)

### Step 2 - Setup your client

Your client device is the device you want to speak into. See below for the quick-start instructions for what I use, Dictate Keyboard on an Android phone.

For live-streaming (see the text as you speak) dictation, point your client's streaming provider base URL at the proxy:

```
http://<local-server-ip>:8084/v1/
```

For batch processing (all at the end), use:

```
http://<local-server-ip>:8816/v1/
```

#### Quick-Start for Dictate Keyboard (uses default settings, i.e. what I think is the best)

1. Install Dictate Keyboard on your Android device
2. Open it, and go through the setup wizard (you can skip adding a provider in the wizard and do it later (step 3 below))
3. Select **Dictate -> AI Providers -> Add a Provider -> Add your own server** from the menu
4. Give your local provider a name (e.g. "streaming-nemotron")
5. Put in the base URL above (http://<local-server-ip>:8084/v1/ )
6. For "Transcription model," enter: `parakeet-cpp-tdt-0.6b-v3`
7. Hit "Test Connection" and "Test transcription" to see if it works
8. Turn "Real-time streaming" on
9. A box for "Real-time model" will appear. You can leave it blank to use the default streaming model (EOU).
10. Leave all other settings blank or default (off).
11. Hit "Ok" to save.
12. Back in **AI providers** set "Transcription" to the provider you just created.
13. Optional: set "Rewording" to another provider (make it the same way as above). This enables Dictate Keyboard's "Fix Grammar," "Improve Writing," and other functions. I've set mine to a free Omniroute combo, but you could also put an OpenRouter, OpenAI, Anthropic, etc base URL and API key.
14. Go up one level in the menu system (i.e. back to **Dictate**) and select **Recording**.
15. Set "Real-time transcription" to "Show the text while I speak."
16. Test it: touch inside the bottom "Try out your setup" textbox and make sure the keyboard that pops up is Dictate. Hit the microphone icon and start dictating. Text should appear as you speak.
17. Optional: Select **Floating dictation button** in the **Dictate** menu to turn it on. Now there will be a floating button like Wispr Flow which lets you dictate from another keyboard, albeit without streaming text.

## Alternate Setup Options

In addition to streaming dictation using the EOU model, there are other options, each with different pros and cons.

### 1. parakeet-api — batch transcription, port 8816

**Pros:**

- Highly accurate: around 6% WER (word error rate), comparable to Wispr Flow in my experience.
- **Automatic punctuation and capitalization.** This is the only path in this repo that punctuates reliably.
- Fast (with the caveat that processing only occurs *afterwards*, not while you're speaking). That said, even on CPU: a 10 s clip took ~1–2 s on my system (8th gen Intel system with no GPU).

**Cons:**

- No live streaming. You hit the button, speak, and then the audio is sent in a batch, processed, and comes back as text.

** More info:**

- Model: **NVIDIA Parakeet TDT 0.6B** (int8, via sherpa-onnx).
  Default is v3 (25 European languages, ~6.32% average WER on the
  Hugging Face Open ASR Leaderboard). For English-only dictation v2
  is slightly more accurate (~6.05% WER) — set
  `STT__SHERPA__MODEL_ID=sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8`
  in `parakeet-api/.env` and download v2 into `parakeet-api/models/`
  (see that folder's README notes).

- In addition to being one of the choices of a transcription provider in Dicate Keyboard, it also functions as the automatic fallback if, for some reason, you're using the live streaming dictation option below, and it fails.

- Test it with `parakeet-api/test-transcribe.sh sample.wav`.

- API: OpenAI Whisper-compatible `POST /v1/audio/transcriptions`,
  optional Bearer API key. `/v1/audio/transcriptions/raw` also
  accepts raw audio bytes. The model name in the request is
  accepted and ignored in favour of the loaded Parakeet model.

### 2. streaming-dictation — live transcription, LocalAI :8081 + wsproxy :8084

Fast AND accurate.

Text appears **as you speak**.

A pause while thinking doesn't end the dictation.

This is what I use because, not only do I like to see the text appear as I speak (and edit it as I go!), it's faster because it's processing while I'm still speaking. The above parakeet option only processes at the end.

**Downsides:**

- It doesn't punctuate or capitalize reliably (or at all). Yet. (See below.)
- Sometimes a few seconds of speech gets lost. (Also see below.)

For streaming-dictation to work, LocalAI (v4.12.1) is used under-the-hood. It runs three pipelines:

| Pipeline                     | Model                                        | Accuracy  | Live words                 | Punct/Capitals    |
|------------------------------|----------------------------------------------|-----------|----------------------------|-------------------|
| `dictate-streaming`          | Parakeet realtime EOU 120M **(recommended)** | 9.30% WER | yes (true live streaming)  | no                |
| `dictate-streaming-tdt`      | Parakeet TDT 0.6B v3                         | 6.32% WER | text appears at pauses     | yes               |
| `dictate-streaming-nemotron` | Nemotron Speech Streaming EN 0.6B            | 6.93% WER | at pauses, slower than TDT | **no — see note** |

The default is Parakeet EOU, but you can set a different pipeline as the default with the `WS_PROXY_MODEL` environment variable. Set it in Dictate Keyboard in each provider in the real-time model field (or leave blank for default).

Note: WER values are vendor-reported, from different eval setups, so while reasonably accurate, it's not truly apples-to-apples or third-party comparison.

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

**Known issue — realtime turn loss (upstream).** During long
multi-turn dictations, a whole turn intermittently returns no
deltas and an empty `completed` (or no `completed` at all), while
surrounding turns work fine. It reproduces across all three
backends (Nemotron, EOU, TDT) and both VAD modes, and a batch
decode of the recorded audio proves the audio was good — the
realtime decoder drops the turn. Filed upstream as
[mudler/LocalAI#13438](https://github.com/mudler/LocalAI/issues/13438);
the full write-up is in `streaming-dictation/localai-issue-draft.md`.
This repo ships the proxy-side session recorder (`recordings/`
next to `wsproxy.py`) that made that proof possible.

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

TL;DR: Live-streaming dictation requires a daemon, wsproxy.py, to be running. `make proxy` in the Quick-Start above does it.

`wsproxy.py` is a small stdlib-only proxy (no pip packages) that sits between Dictate and LocalAI on host port **8084**. It's needed because LocalAI and the models require certain workarounds to work together.

Technical info (what it actually does under-the-hood):

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

Normally, just use `make`, but here's the manual way if you ever need it:

```bash
pkill -f wsproxy.py   # kill first if already running
WS_PROXY_MODEL=dictate-streaming                nohup python3 wsproxy.py > wsproxy.log 2>&1 &
# or: WS_PROXY_MODEL=dictate-streaming-tdt      nohup python3 wsproxy.py > wsproxy.log 2>&1 &
# or: WS_PROXY_MODEL=dictate-streaming-nemotron nohup python3 wsproxy.py > wsproxy.log 2>&1 &
```

##### Debugging Features in wsproxy.py

Since this project is still experimental and under-development, wsproxy.py keeps a log and copies of the raw audio:

- `wsproxy.log`
- Raw recordings are in `recordings/`.

Both are in streaming-dictation/

Manually clear them out to save hard-drive space.

### 3. speaches — Whisper STT, port 8001

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

- **Slower on CPU than Parakeet.** This is why I decided not to use it. It's just too slow. The batch Parakeet TDT above is both faster and more accurate for English. With a GPU, it might be fast enough, and the multilingual/TTS features may become worth it.

## General Dictate Keyboard setup (Android)

Add the services as providers in Dictate's Settings, and use
Dictate as the keyboard on your phone. A Wispr Flow-like floating
button is available too.

- **Batch provider**: use base URL
  `http://<odin-LAN-IP>:8816/v1`, API key from
  `parakeet-api/.env`. ("Test connection" returning
  `{"detail":"Not Found"}` is a red herring; "Test transcription"
  is the real check.)
- **Streaming provider**: Leave blank for default or put in the pipeline name, e.g. `dictate-streaming`, `dictate-streaming-tdt`, or `dictate-streaming-nemotron`
- Note: live text only works with Dictate Keyboard's keyboard. It's deliberately held back (Dictate issue #456) with the floating button and other system keyboards.

### Optional: Add Rewording support to Dictate Keyboard

- **Rewording provider** (for the "Fix Grammar" etc. buttons):
  add a *separate* custom provider pointing at an OpenAI-compatible
  **chat** endpoint (not these voice transcription models). I use OmniRoute on the same host:
  base URL `http://<my-server-ip>:20128/v1`, model `general`,
  OmniRoute client key made in the OmniRoute webui. I made a `general` combo of free models (Gemini 3 Flash
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
endpoint — set `OPENAI_BASE_URL=http://<odin-LAN-IP>:8816/v1` and
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

- If streaming is not working, check:
	- Are you really using the Dictate Keyboard? The floating button and "big orb" dictation keyboard don't stream. Only the microphone icon in the upper-right corner of the Dictate Keyboard will stream.
	- Check for typos in the Dictate Keyboard provider Real-time model setting
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
│   ├── wsproxy.py             # stdlib-only proxy between Dictate and LocalAI
│   ├── models/                # LocalAI model + pipeline YAMLs
│   ├── localai-fix-empty-completed-pnc.patch   # optional, see below
│   └── localai-issue-draft.md # upstream issue text (filed as #13438)
└── speaches/                  # Whisper STT — port 8001 (retired, kept as an option)
```

All three services are Docker Compose projects. `wsproxy.py` is the
only host process (plain Python 3, stdlib only, no pip packages).
