# voice-server

Your own self-hosted, real-time streaming STT (Speech-to-Text) server

## Inspiration

Speaking is faster than typing, especially on a smartphone, but dang, it's frustrating when it makes mistakes. Then Wispr Flow came along, and wow, it was fast AND accurate. But then... I hit the free usage limit and had to go back to inferior Gboard for a while. I missed Wispr Flow bad! Wispr Flow would also time out sometimes. Make me wait almost a minute only to fail. (Maybe it was a victim of its own success, and its servers became overloaded?)

This is why I decided to make my own free, private, local, and open-source version, and in many ways, it's even better than Wispr Flow. Sadly, I couldn't replicate it perfectly, and this repo, in current form, is still experimental and unfinished. However, I do use it as my daily-driver. I uninstalled Wispr Flow and haven't looked back.

## Features

- Fast, accurate voice transcription on your phone.
- Live-streaming text. See the text as you speak. Edit it on the fly.
- Your own server: less downtime (we hope).
- Private. Nothing leaves your local network.

## Alternatives

- Cloud models can do it, but you might have to pay for those too, not to mention the privacy concerns.
- Dictate Keyboard can run a model with all these features on your phone, but unless you have a very modern phone (with good cooling), even my old 8th-gen Intel server can run superior models.
- There are existing local STT servers, including streaming ones, but I did not find a turnkey setup that made Dictate Keyboard's realtime mode work with the fast local models I wanted to use.

In short, to get free, fast, and accurate real-time streaming transcription on my phone, I had to do it myself.

## Prerequisites

- Your own server with static IP address (no ports need to be opened)
	- Docker + Docker Compose (v2)
	- Python 3 (stdlib only)
	- `openssl` (optional, used by `make env` to generate the API key).

## Quick start

### Step 1 - Setup the host (your local server)

Run these in order on the server:

```bash
make env      # create parakeet-api/.env with a generated API key
make up       # start parakeet-api (:8816) and LocalAI (:8081)
make models   # install the LocalAI gallery models (see note below)
make proxy    # start wsproxy.py on :8084 (one candidate list)
make status   # show containers + whether wsproxy is running
```

(Note: Use `make help` for the full list of commands, including `down`, `logs`, `proxy-stop`, and the opt-in `up-speaches`.)

The shipped `DEFAULT_FAILOVER` in `streaming-dictation/wsproxy.py` contains the author's LAN addresses. Edit that list so its entries point at your LocalAI server(s), or pass `WS_PROXY_FAILOVER=...` to `make proxy`. For a single server, a one-entry list such as `dictate-streaming@127.0.0.1:8081` is enough.

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
4. Give your local provider a name (e.g. "streaming-stt")
5. Put in the base URL above (http://<local-server-ip>:8084/v1/ )
6. For "Transcription model," enter: `parakeet-cpp-tdt-0.6b-v3`
7. Hit "Test Connection" and "Test transcription" to see if it works. (This is NOT the streaming model. It's the fallback if streaming fails.)
8. Turn "Real-time streaming" on
9. A box for "Real-time model" will appear. For the recommended live-words setup, enter `dictate-streaming`. If you leave it blank, wsproxy uses the candidate list in order, starting with the first reachable entry.
10. Leave all other settings blank or default (off).
11. Hit "Ok" to save.
12. Back in **AI providers** set "Transcription" to the provider you just created.
13. Optional: set "Rewording" to another provider (make it the same way as above). This enables Dictate Keyboard's "Fix Grammar," "Improve Writing," and other functions. I've set mine to a free Omniroute combo, but you could also put an OpenRouter, OpenAI, Anthropic, etc base URL and API key.
14. Go up one level in the menu system (i.e. back to **Dictate**) and select **Recording**.
15. Set "Real-time transcription" to "Show the text while I speak."
16. Test it: touch inside the bottom "Try out your setup" textbox and make sure the keyboard that pops up is Dictate. Hit the microphone icon and start dictating. Text should appear as you speak.
17. Optional: Select **Floating dictation button** in the **Dictate** menu to turn it on. Now there will be a floating button like Wispr Flow which lets you dictate from another keyboard, albeit without streaming text.

## Alternate Setup Options

In addition to the default models, others are available to experiment with, each with different pros and cons.

### 1. parakeet-api — batch transcription, port 8816

**Pros:**

- Highly accurate: around 6% WER (word error rate), comparable to Wispr Flow in my experience.
- **Automatic punctuation and capitalization.** This is the only path in this repo that punctuates reliably.
- Fast (with the caveat that processing only occurs *afterwards*, not while you're speaking). That said, even on CPU: a 10 s clip took ~1–2 s on my system (8th gen Intel system with no GPU).

**Cons:**

- No live streaming. You hit the button, speak, and then the audio is sent in a batch, processed, and comes back as text.

** More info:**

- Model: **NVIDIA Parakeet TDT 0.6B** (int8, via sherpa-onnx).
  Default is v3 (25 European languages, ~6.34% average WER on the
  Hugging Face Open ASR Leaderboard). For English-only dictation v2
  is slightly more accurate (~6.05% WER) — set
  `STT__SHERPA__MODEL_ID=sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8`
  in `parakeet-api/.env` and download v2 into `parakeet-api/models/`
  (see that folder's README notes).

- This is a separate batch provider. Dictate's automatic batch fallback for a streaming provider uses that provider's base URL; through wsproxy on :8084 it reaches LocalAI, not parakeet-api on :8816.

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

For streaming-dictation to work, LocalAI is used under-the-hood. The reference setup and patch below are based on LocalAI v4.12.1; `docker-compose.yml` currently uses `localai/localai:latest` unless you pin or customize the image. It runs five pipelines:

| Pipeline                     | Model                               | Accuracy  | Live words         | Punctuation       |
|------------------------------|-------------------------------------|-----------|--------------------|-------------------|
| `dictate-streaming`          | Parakeet realtime EOU 120M          | 9.30% WER | yes                | no                |
| `dictate-streaming-whisper`  | Whisper large-v3 (requires GPU)     | 7.44% WER | untested           | yes               |
| `dictate-streaming-tdt`      | Parakeet TDT 0.6B v3                | 6.34% WER | yes, at pauses     | usually           |
| `dictate-streaming-tdt-v2`   | Parakeet TDT 0.6B v2 (English-only) | 6.05% WER | yes, at pauses     | usually           |
| `dictate-streaming-nemotron` | Nemotron Speech Streaming EN 0.6B   | 6.93% WER | per-utterance only | **no — see note** |

There is no single default model setting. wsproxy has one candidate list (`DEFAULT_FAILOVER` in `wsproxy.py`, overridable with `WS_PROXY_FAILOVER`); it checks the list in order and skips down servers. Dictate's Real-time model field is the first choice when it matches a list entry. For the recommended live-words setup, set that field to `dictate-streaming` (Parakeet EOU).

Note: WER values are vendor-reported, from different eval setups, so while reasonably accurate, it's not truly apples-to-apples or third-party comparison.

**Nemotron punctuation — it does not punctuate in practice.**
NVIDIA documents Nemotron as punctuating natively, and a PnC BERT
model is attached in the pipeline, but the streamed `completed`
text is all-lowercase with no punctuation. Confirmed 2026-11-23:
the NeMo-Speech.cpp C++ `Postprocessor` deliberately skips the
attached PnC model at construction because the Nemotron vocab
self-punctuates (its vocab contains `.`, `?`, `!` tokens). A second likely contributor is that sentence-final punctuation is exposed as `late_punctuation`, which LocalAI's Go layer does not read. So the
flag, the file, and the YAML are all correct — and the text is
still unpunctuated. Fixing it needs a C++ change and a rebuilt
backend `.so`, not a config tweak. The batch endpoint above is the
punctuating path; the Dictate rewording provider (below) is the
other way to get cleaned-up text.

**Known issue — realtime turn loss (upstream).** During long
multi-turn dictations, a whole turn intermittently returns no
deltas and an empty `completed`, while
surrounding turns work fine. A batch decode of the recorded audio
proves the audio was good — the realtime decoder drops the turn.
Filed upstream as
[mudler/LocalAI#13438](https://github.com/mudler/LocalAI/issues/13438).
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
`dictate-streaming-nemotron`) and make sure that pipeline is in
wsproxy's candidate list, then run long multi-turn dictations
and check `wsproxy.log` / `compare_recording.py` for empty
`completed` events.

**Optional patch.** A small Go patch against LocalAI v4.12.1 was prepared during development (the patch file is not included in this branch): it skips the `completed`
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

- Injects `?model=<pipeline>` from the chosen candidate in the failover list so LocalAI uses that pipeline.
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
- Plain HTTP (batch transcriptions, `/v1/models`) is forwarded to the first reachable candidate's LocalAI too, so Dictate can point its single base URL here and both paths work. This batch fallback path does not use parakeet-api on :8816.
- **Upstream failover.** wsproxy holds an ordered list of
  (model, server) candidates (`DEFAULT_FAILOVER`, e.g.
  `dictate-streaming-whisper@<fast-remote-ip>:8081, dictate-streaming@127.0.0.1:8081`).
  Dictate's Real-time model field names the phone's preferred model
  and is tried first (entries with that model, in list order), then
  the rest of the list. The bigger models live only on the fast
  remote server, so they are only used when it is up; the main local
  server carries the smaller ones as the fallback. The chosen
  (model, server) is logged per session
  (`WS session model: Dictate field=...; using ...`). See "Second server (fast
  remote, GPU) + failover" below.

Normally, just use `make`, but here's the manual way if you ever need it:

```bash
pkill -f wsproxy.py   # kill first if already running
nohup python3 wsproxy.py > wsproxy.log 2>&1 &
```

wsproxy.py supports these environment variables:

- `WS_PROXY_LISTEN_HOST` (default `0.0.0.0`)
- `WS_PROXY_LISTEN_PORT` (default `8084`)
- `WS_PROXY_FAILOVER` (default: `DEFAULT_FAILOVER` in `wsproxy.py`)
- `WS_PROXY_FAILOVER_TIMEOUT` (default `2` seconds per candidate)
- `WS_PROXY_RECORDINGS_DIR` (default: `recordings/` next to `wsproxy.py`)

To change the failover list permanently, edit `DEFAULT_FAILOVER` in `wsproxy.py`. To override it for one run:

```bash
WS_PROXY_FAILOVER="dictate-streaming-whisper@<fast-remote-ip>:8081, dictate-streaming-nemotron@<fast-remote-ip>:8081, dictate-streaming@127.0.0.1:8081" nohup python3 wsproxy.py > wsproxy.log 2>&1 &
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
use `make up-speaches` if you want it. I found it too slow on my 8th gen Intel server.

**Pros:**

- **Multilingual, including Japanese.** Whisper supports 99
  languages; pick the model per request. This is the proven Japanese path here (see "Japanese support").
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

## Second server (fast remote, GPU) + failover

A second machine with a GPU (a laptop, a spare box) can run a
second LocalAI with GPU acceleration, using the same repo and the
same commands — `make up-gpu` instead of `make up` (the GPU image
via `streaming-dictation/docker-compose.gpu.yml`). In the shipped default list, the fast remote entries come first and the main local EOU entry is last. Each list entry names both a model and a server; the same pipeline name can appear on more than one server only if you configure entries for it on both.

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
(model, server) candidates (`DEFAULT_FAILOVER`, e.g.
`dictate-streaming-whisper@<fast-remote-ip>:8081, dictate-streaming-nemotron@<fast-remote-ip>:8081, dictate-streaming@127.0.0.1:8081`).
Dictate's Real-time model field names the phone's preferred (fast,
best) model and is tried first — entries with that model, in list
order — then the rest of the list. The bigger models live only on
the fast remote, so they are only used when it is up; the main
local server carries the smaller fallback entry in the shipped list (EOU; add TDT entries if you want them as further fallbacks).
A down candidate delays fallback by about the per-candidate timeout (~2 s by default) for each down candidate tried before a reachable one. The chosen (model, server) is logged per session
(`WS session model: Dictate field=...; using ...`). If the fast remote goes
down mid-session, that session breaks and Dictate falls back to
batch — the next session picks the live box again.

Setup (on the fast remote): `make up-gpu`, then `make models`
(first run) and `make models-gpu` for Whisper. The GPU
prerequisites: the NVIDIA Container Toolkit installed so Docker
can see the GPU (`docker exec localai nvidia-smi` should list it),
and a static IP or DHCP reservation for the fast remote, so the
failover list's address does not move. Wiring up the failover (on
the main local server): `make proxy-failover` (or restart wsproxy
with `WS_PROXY_FAILOVER="<list>"`). Edit `DEFAULT_FAILOVER` in `streaming-dictation/wsproxy.py` if the fast remote's IP differs from the shipped default.

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

**OmniRoute (optional external chat gateway used in my setup).** OmniRoute routes
OpenAI-compatible HTTP calls (chat/completions) to free/cheap
cloud models. It is not an STT gateway: it does not proxy the
realtime WebSocket protocol, and the free cloud providers behind
it do not offer realtime streaming STT. It *could* route a batch
transcription call (e.g. to Groq Whisper) as an alternative batch
provider, but this repo already has a good local one (:8816), so
there is no reason to. The main-local/fast-remote failover is done
directly in wsproxy, not via OmniRoute, for the same reason.

## General Dictate Keyboard setup (Android)

Add the services as providers in Dictate's Settings, and use
Dictate as the keyboard on your phone. A Wispr Flow-like floating
button is available too.

- For the base URL, put: `http://<main-server-IP>:8084/v1/`
- Turn real-time streaming ON
- **Streaming provider**: put in your preferred pipeline name, e.g. `dictate-streaming`, `dictate-streaming-tdt`, `dictate-streaming-tdt-v2`, `dictate-streaming-nemotron`, or `dictate-streaming-whisper`. The name must match an entry in wsproxy's candidate list to be tried first; fallback to other list entries still occurs if that candidate is unavailable. Leave it blank only if you want wsproxy's list order.
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

### Alternate non-streaming (i.e. batch transcription) provider setup

- **Batch provider** ("main"): custom server, base URL
  `http://<main-server-IP>:8816/v1`, API key from
  `parakeet-api/.env`. ("Test connection" returning
  `{"detail":"Not Found"}` is a red herring; "Test transcription"
  is the real check.)

## Other clients

Anything that speaks the OpenAI audio API works against the batch
endpoint — set `OPENAI_BASE_URL=http://<main-LAN-IP>:8816/v1` and
the key. Desktop dictation apps that accept a custom
OpenAI-compatible endpoint include **Voxis** (Windows/Mac/Linux)
and, on Linux, **hyprwhspr** — point them at the same base URL +
key. On the programmatic side the same base URL works with any
OpenAI SDK.

## Why this exists

This project does not introduce a new speech-recognition model, and
it is not the first local streaming STT server. LocalAI already
provides an OpenAI Realtime API, and projects such as WhisperLiveKit
and Speaches provide self-hosted streaming transcription.

The missing piece was narrower: a turnkey way to make Dictate
Keyboard's realtime mode work with a CPU-fast local model on a home
server. Dictate connects without LocalAI's required `?model=`
parameter and sends `turn_detection: null`. LocalAI needs the model
parameter to select the pipeline, and stripping `turn_detection: null` lets the pipeline keep its own turn detection. In the original setup, a blank Real-time model field also skipped the model rebuild that enabled live deltas, and LocalAI's reply to Dictate's stop-commit is an error Dictate treats as fatal.

voice-server supplies that missing glue. `wsproxy.py` translates
between the two programs' assumptions, while the stack pairs EOU's
CPU-fast live words with a Parakeet TDT batch fallback for accuracy
and punctuation. The result is private, subscription-free Android
dictation with text appearing as you speak—and no requirement that
silence end the dictation.

## Exposure

LAN-only by default — **do not port-forward 8816, 8081, 8084, or 8001 (speaches, if used)**.
Away from home, reach the host over Tailscale/WireGuard to the
host's LAN IP. The API key is a second layer, not a substitute for
keeping the services off the WAN. The plain-HTTP warning Dictate
shows for these providers is fine on your own network.

## Japanese support

Short version: **batch Japanese works today; live Japanese
streaming does not.**

- **Batch (works):** point a client at **speaches** (`:8001`) and
  request a Whisper model — Whisper is multilingual and handles
  Japanese. parakeet-api's shipped Parakeet models do not add Japanese; a different sherpa-onnx model would need separate testing.
- **Streaming (open gap):** there is no tested Japanese streaming pipeline here. Parakeet TDT v2 is English-only; TDT v3 covers 25 European languages; Nemotron streaming is English-only; the EOU 120M model is English-only. `dictate-streaming-whisper` is Japanese-capable in principle because Whisper is multilingual, but it is untested in a realtime pipeline here.
- **Rewording in Japanese:** the OmniRoute `general` chain above
  handles Japanese text fine — the gap is transcription, not
  the rewording step.

## Troubleshooting

- If streaming is not working, check:
	- Are you really using the Dictate Keyboard? The floating button and "big orb" dictation keyboard don't stream. Only the microphone icon in the upper-right corner of the Dictate Keyboard will stream.
	- Check the `WS session model:` line in `wsproxy.log`: it shows the Dictate field and the candidate actually used. The field must match a candidate-list entry to be tried first.
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
│   ├── docker-compose.gpu.yml # GPU override (fast remote) — use with -f
│   ├── wsproxy.py             # stdlib-only proxy between Dictate and LocalAI
│   ├── compare_recording.py   # compare streaming vs batch for a recorded session
│   └── models/                # LocalAI model + pipeline YAMLs
└── speaches/                  # Whisper STT — port 8001 (optional)
    └── compose.yaml
```

All three services are Docker Compose projects. `wsproxy.py` is the
only host process (plain Python 3, stdlib only, no pip packages).
