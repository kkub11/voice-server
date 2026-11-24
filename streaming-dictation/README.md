# Streaming dictation test — Dictate + LocalAI on odin

Goal (Kanon's spec): text appears AS he speaks, no send step, and a
pause while thinking must never end the dictation.

## The two pipelines (try B first — it is the one built for this)

**B — `dictate-streaming` (this folder's YAML, live partials).**
Parakeet `realtime_eou_120m-v1`, a 120M model trained to emit an
end-of-utterance token and live transcript deltas, with `semantic_vad`
turn detection. The model distinguishes "paused, expecting a reply"
from "paused mid-thought", so thinking pauses don't cut the turn off.
Fallback silence window is 4 s (eagerness medium); after the
end-of-utterance token the turn commits in ~300 ms. Trade-off: a
smaller model than the 0.6B TDT, so accuracy may be a notch below the
batch server — the test will show whether it matters.

**A — gallery preset `qwen3-streaming-assistant` (fallback).**
Silero VAD + Parakeet TDT 0.6B v3 + Qwen3 0.6B + Qwen3-TTS 0.6B.
The TDT v3 is an *offline* model: each VAD-committed turn is
transcribed as a whole, so no live partials — the transcript arrives
at the end of the turn, but the audio was already streamed, so there
is no separate upload + transcribe wait after you stop. Best accuracy
of the two. Use this if B's accuracy disappoints.

## Setup (on odin)

1. Copy this folder to odin next to the parakeet-api folder.
2. Copy `models/dictate-streaming.yaml` is already inside `./models/`
   — LocalAI reads `*.yaml` there as a model. (If you start the
   container first and add the file later, restart it.)
3. `docker compose up -d`
4. Install the component models (one command each; each downloads
   weights on first use). NOTE: the binary is `/local-ai`, not on
   PATH — plain `local-ai` fails with "executable file not found":
   ```
   docker exec localai /local-ai models install silero-vad-ggml
   docker exec localai /local-ai models install parakeet-cpp-realtime_eou_120m-v1
   docker exec localai /local-ai models install parakeet-cpp-tdt-0.6b-v3
   docker exec localai /local-ai models install qwen3-0.6b
   docker exec localai /local-ai models install qwen3-tts-cpp-customvoice --variant qwen3-tts-cpp-customvoice
   docker exec localai /local-ai models install qwen3-streaming-assistant
   ```
   (The EOU model is pipeline B; the TDT v3 model is pipeline A's
   transcription stage — both are needed if you want both pipelines.
   Same six can be installed from the web UI at
   http://192.168.1.137:8081 → Models, if the CLI names don't resolve.)
5. Check it answers: `curl http://192.168.1.137:8081/v1/models` should
   list the installed models, including `dictate-streaming` and
   `qwen3-streaming-assistant`.

## Dictate settings (phone) — field by field

Open the `odin` custom provider (or a second provider named
`odin-streaming` so batch stays one tap away) and set:

- **Server/host:** `http://192.168.1.137:8083/v1/` — the **proxy** port,
  not LocalAI's own 8081. Include the trailing `/v1/`, per Dictate's
  own docs (it appends `audio/transcriptions` and `chat/completions`
  to the base URL, and the realtime address is derived from it the same
  way). A base URL without `/v1/` still works for batch but [Likely]
  breaks the realtime address.
- **API key:** blank is fine — LocalAI has no auth set, and the batch
  requests already succeed with it blank, which proves auth is not
  the blocker. If streaming still never starts, try `not-needed` as a
  30-second experiment: it is the one field that differs between the
  working batch path and the never-attempted realtime path, and a
  dummy value is harmless against a server with no auth.
- **Transcription model:** `parakeet-cpp-realtime_eou_120m-v1` — the
  STT model actually installed. (An earlier version of this guide
  wrongly said `parakeet-cpp-tdt-0.6b-v3` here, but that model was
  never in the install list — use the EOU name unless you also install
  the TDT v3. Tap the search icon in the field to fetch the server's
  `/v1/models` list; whatever it shows is what is installed. Leave
  this empty and the test comes back "verified, but no text came back".)
- **Real-time streaming:** ON
- **Real-time model:** `dictate-streaming` (pipeline B, live partials).
  If the mic button still errors with this, try
  `qwen3-streaming-assistant` (pipeline A) instead.

**The global switch — this is the one that was missing (found
2026-11-15 22:13 PST by reading Dictate's source):** the per-provider
"Real-time streaming" toggle alone does NOTHING. Dictate also has a
global switch, **Settings → Recording → "Real-time transcription"**,
which is **Off by default**. Tapping the row opens a dialog with
three choices — pick **"Show the text while I speak"**. Do NOT pick
"Show the text only when I stop": that mode streams but hides the
words until you stop, which looks exactly like the batch behaviour.
In code (`DictateController.realtimeApiForActiveAccount()`), the
global switch being off makes Dictate return null and never open the
websocket at all — which is exactly what the LocalAI log showed
(zero realtime requests despite the provider toggle being on).
- **Rewording model:** leave empty — LocalAI has no rewording LLM
  configured for this; rewording stays off for the test.
- **Single-call multimodal:** OFF (that is for audio-capable chat
  models, not an STT server).
- **Wake up early:** OFF (odin is always on).
- **Trust user certificates:** OFF (plain http, no TLS).
- **Request timeout:** 120 s is fine.

Then tap OK, run the transcription test again (it should now return
text), and hold the mic key to speak. If the mic button still errors,
run `docker logs localai --tail 60` on odin right after and send the
lines — they show whether LocalAI rejected the connection (unknown
model / unknown intent) or something else. The empty Real-time model
is the most likely cause: LocalAI picks the pipeline from the model
name, and with the field blank Dictate sends none.

## What "works, but all at once at stop" means (found 2026-11-15 20:15 PST)

If speaking works but the text only appears when you hit stop, Dictate
is falling back to batch — record, then send the whole clip at stop —
and the realtime websocket is not delivering live deltas. There is no
second streaming switch in Dictate; the "Real-time streaming" toggle is
the only one, and if it is on, the problem is server-side. Prime
suspect: the URL shape. Dictate connects
`ws://host/v1/realtime?intent=transcription`, but LocalAI's docs only
show `ws://host/v1/realtime?model=<pipeline>` — with no model in the
URL, LocalAI may reject the session (or load nothing), and Dictate
silently falls back to batch. No Dictate setting changes the URL, so
the decisive check is the log: run
`docker logs localai --tail 80` right after pressing the mic and look
for the websocket connect and any model/intent error. If the log shows
the session failing on the URL shape, the fix is a tiny rewrite proxy
on odin in front of LocalAI (rewrites `?intent=transcription` to
`?model=dictate-streaming`) — not built yet; the log decides.

## Test transcription "verified, but no text" — second cause (2026-11-15 20:15 PST)

The EOU model is a streaming model and [Likely] cannot do the batch
file transcription Dictate's test uses — the request succeeds but
returns empty text. Fix: install the TDT v3 model
(`docker exec localai /local-ai models install parakeet-cpp-tdt-0.6b-v3`)
and set the **Transcription model** field to
`parakeet-cpp-tdt-0.6b-v3`. (The EOU model stays as the Real-time
pipeline's transcription stage — it is only the batch test that needs
the TDT model.) **Confirmed working 2026-11-15 20:21 PST**: the log
shows TDT v3 loading at 04:20:36 UTC and `/audio/transcriptions`
200s right after.

## Log finding: Dictate never opens the realtime connection (2026-11-15 20:23 PST)

`docker logs localai --tail 80` after a mic test shows **zero**
websocket/realtime requests — every request is a batch
`POST /audio/transcriptions` (200s) or a `GET /v1/models`. The
`dictate-streaming` pipeline loads fine, the models load fine, and
batch works. So LocalAI is not rejecting anything: **Dictate is not
even trying to stream.** That rules out the rewrite-proxy idea —
there is no connection to rewrite.

The 404s on `/audio/transcriptions` earlier in the log were requests
for a model LocalAI did not have yet (the TDT name before it was
installed); they stop once the model is installed.

Most likely cause, given there are now two odin provider entries:
the mic is using a different provider entry than the one with
"Real-time streaming" turned on — e.g. the original `odin` entry was
repointed to :8081 (so the mic hits LocalAI, but with realtime off)
while the toggle is on in the separate `odin-streaming` entry the mic
never uses. Check in Dictate's main settings which provider is
selected for transcription, and make sure **that** entry has
Real-time streaming ON and Real-time model `dictate-streaming`
(pick it with the search icon rather than typing, if offered).
Force-stop and restart Dictate after changing provider settings.
Then press the mic once and re-check the log: a working realtime
session shows a websocket request to `/v1/realtime`.

## "model is not a pipeline" (model="") — the proxy (found 2026-11-15 22:21 PST)

Once the global switch is on, Dictate DOES connect — but the log shows
`ERROR model is not a pipeline model=""` on every `/v1/realtime`
request, followed by a batch fallback. Read in both sources, the cause
is exact: LocalAI reads the pipeline name **only** from the `?model=`
query parameter of the websocket URL
(`core/http/endpoints/openai/realtime.go: model := c.QueryParam("model")`),
and Dictate's realtime client always connects to
`ws://host/v1/realtime?intent=transcription` with no model — the model
name from the Real-time model field goes into the `session.update`
message, which LocalAI never consults for pipeline selection. There is
no Dictate field for the realtime URL, and the model can't be smuggled
into the base URL either (batch URLs are plain string concat off it).

The fix is the nginx proxy in this folder (`nginx.conf`, service
`proxy` in `docker-compose.yml`, host port **8083**): it adds
`?model=dictate-streaming` to the realtime handshake and passes
everything else through to LocalAI unchanged. **Dictate's base URL
must therefore be `http://192.168.1.137:8083/v1/`** — the proxy, not
LocalAI directly. After `docker compose up -d`, verify the proxy
answers: `curl http://192.168.1.137:8083/v1/models` should list the
models, and a mic press should show a `/v1/realtime` request in the
log with no "not a pipeline" error after it.

Two proxy bugs, both fixed in the current files (2026-11-15 22:41 and
22:46 PST): (1) the first `nginx.conf` put `:8080` in both the
`upstream` block and the `proxy_pass` lines — nginx refuses that
("upstream may not have port") and the container restart-looped;
the upstream block is gone now. (2) A static `proxy_pass` URI makes
nginx append the client's original query string, so LocalAI received
`model=dictate-streaming?intent=transcription` and read the model
name as `dictate-streaming?intent=transcription` — the fix builds the
address with a variable (`$backend`), which nginx passes exactly as
written, original query dropped, with Docker's embedded DNS
(`resolver 127.0.0.11`) for the name lookup.

## Why Dictate never showed live text — the turn_detection conflict (2026-11-15 23:00 PST)

The sessions after the TTS fix were actually connecting and warming up
fine — the missing piece was *whose job turn detection is*. Dictate's
realtime client hard-codes `"turn_detection": null` in its
`session.update` ("No server-side turn detection: Dictate decides when
a dictation ends and commits the buffer itself" — it does not want the
server cutting turns on its own schedule). LocalAI only opens its
live-transcription stream — the thing that emits word-by-word
`...transcription.delta` events — inside its server-side turn-detection
loop (`semantic_vad`); with turn detection off, audio just accumulates
until the client commits and the transcript arrives whole at stop.
Proven from both codebases: LocalAI's `SessionAudioInput` even has a
`TurnDetectionSet` flag that is true when the key is present *even as
null*, and `updateTransSession` then sets the session's turn detection
to nil, so `turnDetectionActive` is false and the VAD loop never runs.

The fix is `wsproxy.py`: a small stdlib-only proxy (no pip packages)
that sits between Dictate and LocalAI and strips the
`"turn_detection": null` key from Dictate's `session.update` as it
passes through, so LocalAI keeps the pipeline's own `semantic_vad`
and the live deltas flow. Everything else is forwarded byte-for-byte,
and plain HTTP (batch transcriptions, `/v1/models`) is forwarded to
LocalAI too, so Dictate can point its single base URL here and both
paths work. Verified locally 2026-11-15 with `test_wsproxy.py`
(fake upstream + fake Dictate client, 8/8 checks: key stripped, model
and format intact, audio append forwarded, delta + completed events
returned, `/v1/models` proxied).

Run it on odin (from `~/streaming-dictation`):

    nohup python3 wsproxy.py > wsproxy.log 2>&1 &

then point Dictate's odin-streaming base URL at the proxy:

    http://192.168.1.137:8084/v1/

(the transcription model, realtime model, and the global Recording
switch stay exactly as they are). The nginx proxy on :8083 is no
longer needed for Dictate once :8084 is in use — leave it running
or stop it, it costs nothing either way. `wsproxy.log` should show a
`REWRITE session.update: stripped turn_detection:null` line on each
mic press, and LocalAI's log should show `semantic_vad: committing
turn` lines as turns close.

Behaviour change to expect: with the server cutting turns, a
mid-thought pause of ~4 s or more (the medium-eagerness fallback
window) may split the text into two chunks — but the dictation stays
open until *you* stop it; silence never ends it. The final chunk
lands ~300 ms after you finish a sentence (EOU token) or up to ~4 s
after a trailing pause with no EOU.

## The stop-commit error — second incompatibility (2026-11-15 23:14 PST)

The first wsproxy test connected and rewrote correctly (the proxy log
showed the REWRITE line), but the session died after 11 s with an
ABNORMAL close (LocalAI: `close 1006 (abnormal closure)` — no close
frame, unlike the clean 1000s before). Cause, from the code: when the
user stops, Dictate sends `input_audio_buffer.commit`; with server
turn detection on, LocalAI answers that commit with an *error* event
(`code: not_implemented` — `sendNotImplemented` is just `sendError`),
and Dictate treats ANY error event as fatal (`emitError` → `ws.cancel()`
→ abrupt close → batch fallback). So `wsproxy.py` now also drops
LocalAI's `not_implemented` error events on the way back to Dictate
— just that one code, every other error passes through — so Dictate
waits for the server's own turn commit to deliver the final text as
a normal `completed` event. (Verified in `test_wsproxy.py`: 10/10.)

Still open after that fix: no live deltas appeared during the 11 s,
and LocalAI's INFO log shows no turn commits. The INFO log cannot say
why — the VAD loop, the live-session open, and the turn commits only
appear at debug level. Diagnostic: the compose file now carries a
temporary `command: ["--log-level=debug"]` on the localai service
(the entrypoint passes args through to `./local-ai`). Run
`docker compose up -d` to apply it, press the mic once, then send
`docker logs localai --tail 150` — it will show whether the VAD loop
starts, whether the live transcription opens or degrades to
silence-only, and whether any turn commits. Remove the command line
once streaming is diagnosed (it makes the log very noisy).

## Debug log, round 1 — the pipeline is too slow (2026-11-15 23:23 PST)

The debug log proved the rewrite works and the live path is alive:
semantic_vad is active, Silero finds the speech segments, the EOU
model IS decoding the audio (`live feed ... fed_s` growing 4.08 ->
7.12, catching up after the session ends), the stop-commit error is
swallowed (clean close 1000 now, no more 1006), and the batch
fallback transcribed correctly ("Well, so let's try this again and
see if it worked."). But no live delta ever left the server in a
~10 s dictation. Why: each Silero VAD pass over the 5 s window took
3.2-4.0 s of wall time, so the turn-detection tick loop runs at
~0.3 Hz, and the live decode lags ~1 s behind real time
("live decode is falling behind real time"). LocalAI is capped at
3 CPUs while Frigate uses ~4.2 cores on the 6-core i7-8700. Two
speed fixes in the current files: `cpus: 3.0 -> 4.0` in
docker-compose.yml, and `eagerness: medium -> high` in
models/dictate-streaming.yaml (2 s silence fallback instead of 4 s,
which also shrinks the per-tick VAD scan window from ~5 s to ~3 s).
`wsproxy.py` now also logs every event LocalAI sends (UPSTREAM
delta/completed/error lines in wsproxy.log), so the next mic test
shows directly whether live words flow at all. If the VAD passes are
still multi-second after these two changes, the next lever is the
VAD backend itself (Silero currently runs through the whisper
backend) or a lighter turn-detection mode.

## CORRECTION — the mode was never the cause (2026-11-15 23:39 PST)

The "you're on 'Show the text only when I stop'" explanation above
was wrong. Kanon checked: his Recording settings ALREADY show
"Show the text while I speak". That was a guess with no evidence
behind it, and the screenshot proves it. The deltas genuinely reach
Dictate (they are forwarded byte-for-byte, and LocalAI's delta event
type was verified correct against its own source:
`conversation.item.input_audio_transcription.delta` with item_id,
content_index 0, delta, event_id) -- so the display failure is
downstream of delivery.

## New lead: LocalAI's placeholder event_id (2026-11-15 23:39 PST)

Every event LocalAI sends carries the literal placeholder
`event_id: "event_TODO"` (core/http/endpoints/openai/
realtime_transcription.go + types/server_events.go). If Dictate
tracks or dedupes events by event_id, everything after the first
event is dropped -- which looks exactly like what Kanon sees: the
stream runs, nothing displays. The other candidate is an item_id
mismatch, which needs the raw event to check. The proxy now does
both halves: `log_upstream_event` logs the FULL JSON of the first
delta event per session (so item_id, event_id and every field are on
record in wsproxy.log), and `rewrite_upstream_event` stamps unique
`event_0001`, `event_0002`, ... ids on every upstream event --
top-level, and nested inside the `error` object where LocalAI puts
it on error events (an error event with no top-level id also gets
one added). The `not_implemented` drop is unchanged. Test suite
extended to 12 checks, all passing (event_id rewrite incl. the
nested error case, event-less passthrough, first-delta raw log).
To test: `pkill -f wsproxy.py`, relaunch, one mic test, paste
wsproxy.log -- look for the `UPSTREAM first delta raw:` line and
whether the words show in Dictate this time.

## "realtime warmup failed" — the TTS stage (2026-11-15 22:51 PST)

Once the model name arrives intact, the session opens and LocalAI
warms up every stage of the pipeline — and the warmup fails the whole
session if any single stage fails to load. The `qwen3-tts-cpp-customvoice`
backend crashes on load (process exits, "error reading from server:
EOF"), so every realtime session died at warmup even though the
transcription stages loaded fine. A transcription-only session needs
no TTS, so the `tts:` line was removed from
`models/dictate-streaming.yaml` — the pipeline is now VAD +
transcription + LLM only. After editing the file, restart LocalAI so
it re-reads the pipeline: `docker compose restart localai`. (Pipeline
A, `qwen3-streaming-assistant`, has the same TTS stage and would fail
the same way — it is not a workaround for this.)

## What to observe (report back)

- Does text appear word-by-word WHILE you speak (pipeline B), or
  only after you stop (pipeline A behaviour)?
- Speak a sentence, pause 2–3 s mid-thought, then continue: does the
  pause end the turn, or does it keep listening?
- Accuracy vs batch on your voice: any more mistakes than usual?
- End-to-end feel: is the text complete essentially the moment you
  release the mic key?

## If it fails — the exact error matters

The one thing the docs do NOT establish is whether LocalAI accepts
Dictate's `?intent=transcription` websocket URL (the docs only show
`?model=<pipeline>` and reference transcription-only sessions without
giving a creation method), and whether a transcription-only session
loads the LLM. So if Dictate errors, or LocalAI's logs
(`docker logs localai`) show `model_load_error` / unknown model /
unknown intent, copy the exact message — that IS the test result,
and it decides between: (a) point Dictate at `?model=dictate-streaming`
  if it lets you set the model, (b) a thin websocket bridge speaking
  Dictate's protocol over the streaming model, or (c) staying on batch.

## Standing rules for this test

- Batch parakeet-api on :8816 stays running and untouched — flip
  Dictate back to it any time; it remains the proven fallback.
- Kokoro + Open WebUI stay parked until this test lands (Kanon's call).
- LocalAI's realtime pipeline is a full voice-assistant pipeline
  (VAD + STT + LLM + TTS with barge-in) — a possible later front-end
  for a truly interruptible companion, but NOT this test.

## event_id was not it either (2026-11-15 23:45 PST)

The rewrite ran (session.created -> event_0001) and the deltas
still flowed -- "all right so here's another mic test to see if it's
streaming it is not streaming even though it's gone over ten
seconds" -- and Dictate still shows nothing live. The first delta's
raw JSON is now on record and is the correct OpenAI shape
(item_id item_e599101433f88d574b6efb19be0a0b86, content_index 0).
Verified in Dictate's source (RealtimeClient.kt onMessage): the
delta handler calls onPartial unconditionally -- no event_id dedupe,
no item_id check -- so the deltas reach Dictate and the break is
inside its display path, not the wire. Remaining suspects in
DictateController.showLive: outputTarget IME vs OVERLAY (a
floating-button dictation hides the field preview by design and
captions the bubble instead -- overlayPreviewWanted), the sink /
setDictationPreview write, or the selected-text / handover guards.
Next: ask Kanon which mic he presses, where he looks, and whether
the final text lands after stop. Note: no batch POST followed this
session (finalize-from-partials, or the log was pasted early), and
still no completed event -- the turn commit keeps racing Dictate's
~2 s tail wait after stop.

## Screenshot: both buttons tried, nothing anywhere until stop (2026-11-15 23:51 PST)

Kanon usually presses the floating Dictate button (OVERLAY) but
tried the keyboard mic too -- neither streams. The orange bubble
shows only the timer + volume line, no text; nothing appears in the
text box until he hits stop. Recording settings visually confirmed
again: "Show the text while I speak". Verified in source:
OpenAiRealtimeSession IS the class used for custom servers, its
onMessage calls onPartial unconditionally, and stopRealtimeAndFinalize
commits realtimeTranscript immediately when non-empty, falling back
to batch WAV transcription only when the stream produced nothing
(or the tail wait was incomplete). Decisive next test: watch WHEN
the text lands after stop -- immediately means the partials arrived
and only the display path is broken; ~30 s later plus a batch POST
in wsproxy.log means the partials never reached the controller.

## 1 second after stop -- the pipeline works, only the display is broken (2026-11-15 23:56 PST)

Kanon: text lands 1 s after stop even for a 20 s dictation. That
can only be the realtime finalize path -- the batch fallback took
~30 s in the earlier test, so a 1 s landing means the partials DID
arrive, showLive ran, and realtimeTranscript accumulated (verified:
showLive is the sole writer of realtimeTranscript in the realtime
path; long-form is Off, so the segmented path is excluded). The
server + proxy work end to end. What is broken is ONLY the live
display: the bubble caption and the field preview both show nothing
while speaking. That failure is inside Dictate's display layer --
the sink / preview-surface writes are runCatching-swallowed, and
the bubble caption depends on UI state (added/dimmed) the server
cannot see -- so no further proxy change can fix it blind. Next
step is runtime evidence from the phone (logcat during one mic
test) or a Dictate issue.

## FOUND IT: issue #456 -- floating button + FUTO, by design (2026-11-16 00:04 PST)

Kanon's last note before bed: none of the Recording settings seem
to have any effect. Checked Dictate's GitHub issues -- issue #456
("Fix real-time transcription preview when dictating via floating
button over 3rd-party IME", closed 2026-10-21) is his situation
verbatim: floating button + FUTO active -> live deltas never appear
in the field, only the final text on Stop; dictating inside
Dictate as the active IME streams fine. The maintainer confirmed
the hold-back is DELIBERATE (#357): live-typing into another app's
field through accessibility is unreliable (a refused write leaves
later updates computed against text that isn't there -- "the text
stopping mid-sentence and never catching up"). The replacement is a
live caption beside the bubble (#453) -- on main, but in NO
released version yet (latest release v6.4.0, 2026-10-07; the
caption landed on main ~Oct 21 as "the next release"). So Kanon's
build almost certainly has no caption: bubble shows timer + volume
only, by version, not a bug. The "settings ignored" read is largely
explained: the Recording toggles he tried don't control the
preview, the mode is set right, and the preview is gated by the
#357 design decision -- no issue found about Recording settings
being ignored wholesale. Morning checks: (1) his Dictate version
(if <= 6.4.0 the caption isn't in it yet); (2) clean IME test --
Dictate as the active keyboard + its mic should stream live into
the field per #456.

## IT WORKS -- Dictate keyboard as the active IME (2026-11-16 00:26 PST)

Kanon switched to the actual Dictate keyboard as the active IME
and live streaming works. (His earlier "Dictate keyboard" wasn't
actually Dictate -- FUTO was still the active keyboard.) The
floating-button path stays as #456/#357 describe: field preview
held back by design, caption only in unreleased main.

## Punctuation is gone in the streaming model (2026-11-16 00:26 PST)

New question from Kanon: punctuation stopped working. Evidence
from his own test logs: the EOU 120M streaming model's deltas are
raw lowercase with no punctuation ("all right so we're looking for
deltas are there going to be any deltas ..."), while the batch TDT
v3 transcript punctuates ("Well, so let's try this again and see if
it worked."). So the punctuation loss is the streaming model
itself, not the pipeline. Open: the completed event might
punctuate even if the deltas don't -- one was never observed (the
turn commit keeps racing Dictate's ~2 s tail wait). Options: (a)
Pipeline A (TDT v3 + server_vad) punctuates but gives no live
word-by-word; (b) a punctuation-restoration step at finalize
(Dictate rewording via the pipeline's qwen3-0.6b is weak; a
dedicated punctuator service on odin is the real fix).

## Punctuation fix test — Nemotron streaming model (2026-11-16)

The EOU 120M model cannot punctuate, by design: NVIDIA's own NeMo
voice-agent README says it "does not support punctuation and
capitalization". The replacement is NVIDIA's other streaming model,
**Nemotron Speech Streaming EN 0.6B** — same streaming family, but it
punctuates and capitalizes natively and is more accurate (6.93% WER
at 1.12 s lookahead vs the EOU model's 9.30%). ~700 MB at int8, and
the ptw research doc measured 7-9x realtime on a mediocre laptop CPU.
LocalAI already ships the backend for it (nemo-speech-cpp), which
implements the same live-transcription RPC as parakeet-cpp — verified
in LocalAI's source (backend/go/nemo-speech-cpp/asr_stream.go).

One feel difference to expect (from LocalAI's nemo-speech-cpp doc):
deltas arrive per FINALIZED utterance, not word-by-word mid-sentence.
The backend drops interim hypotheses on purpose. So text lands as
punctuated sentences at utterance ends rather than as a live word
stream. The turn still commits on the 2 s silence window (no EOU
token), and Dictate still holds the dictation open until you stop it.

Install on odin, from ~/streaming-dictation:

  # 1. Backend (once) — or install it from the web UI, Backends page:
  docker exec localai /local-ai backends install nemo-speech-cpp

  # 2. The GGUF (~700 MB) into ./models/ (already published on HF):
  cd ~/streaming-dictation/models
  curl -L -o nemotron-speech-streaming-en-0.6b.q8_0.gguf \
    https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b/resolve/main/nemotron-speech-streaming-en-0.6b.q8_0.gguf
  # (If curl 404s, fallback: pip install huggingface_hub, then
  #  hf download nvidia/nemotron-speech-streaming-en-0.6b \
  #    nemotron-speech-streaming-en-0.6b.q8_0.gguf --local-dir .)

  # 3. Copy the two new YAMLs into ./models/ (they are in this zip):
  #    models/nemotron-speech-streaming-en-0.6b.yaml
  #    models/dictate-streaming-nemotron.yaml
  #    (the EOU pipeline stays in place as the fallback)

  # 4. Restart LocalAI so it picks up the new model + pipeline:
  cd ~/streaming-dictation && docker compose restart localai
  #    check it loaded: curl http://192.168.1.137:8081/v1/models
  #    should list nemotron-speech-streaming-en-0.6b and
  #    dictate-streaming-nemotron

  # 5. Point the wsproxy at the new pipeline (it already supports
  #    this env var — no file change needed):
  pkill -f wsproxy.py
  WS_PROXY_MODEL=dictate-streaming-nemotron \
    nohup python3 wsproxy.py > wsproxy.log 2>&1 &

Dictate needs NO change: the Real-time model field is cosmetic here
(LocalAI takes the pipeline from the proxy's handshake URL, not from
Dictate's session.update). Keep the Dictate keyboard as the active
keyboard, press the mic, and report: (a) does punctuated text land,
(b) does it arrive per sentence rather than word-by-word, (c) how far
behind real time does it feel, (d) accuracy vs the EOU model.
wsproxy.log will show the deltas; docker logs localai --tail 100
shows the session. If the lag is bad, the lever is CPU (Nemotron is
0.6B vs the EOU model's 120M — 5x the weights).

If Nemotron works, the EOU pipeline becomes the fallback and the
--log-level=debug flag in docker-compose.yml should come out (it
makes the logs very noisy). If it doesn't, the fallback options are:
Pipeline A (TDT v3, punctuates, text only at pauses, no live words)
or Dictate rewording pointed at the laptop's LM Studio Qwen3-8B
(see below).

## Rewording — why there is no model to pick (2026-11-16)

Rewording needs a chat-completions model, and the rewording MODEL
setting now lives in the provider ACCOUNT (Dictate moved it there
2026-11-04; the old top-level pref is deprecated). For the
odin-streaming provider the model list comes from LocalAI's
/v1/models — and the only chat model in it is qwen3-0.6b, a tiny
0.6B LLM that would reword badly. That is why the picker looks
empty of anything suitable.
Auto-formatting is the same story: it is an LLM rewording pass
(buildAutoFormattingPrompt), not a rule-based fixer — without a
rewording model it just notes "skipped rewording".
The usable rewording model you already have is the laptop's LM
Studio Qwen3-8B: add it as a separate rewording provider (custom
server, base URL http://192.168.1.236:1234/v1, model qwen3-8b).
It would punctuate at finalize, in a few seconds, but it rewrites
wording (not just punctuation) and needs the laptop on — a
workaround, not the fix. Nemotron above is the fix.

## Speaches — what it is (2026-11-16)

Speaches (speaches-ai/speaches, formerly faster-whisper-server) is a
self-hosted OpenAI-compatible speech server: faster-whisper for STT,
Kokoro/Piper for TTS, plus a realtime WebSocket at /v1/realtime.
Current release v0.9.0-rc.3 (Dec 2025); development has been quiet
since Apr 2026. We never tried it. The Whisper route lost in Oct
2026 because Whisper large-v3 is too slow on odin's i7-8700 and
scores worse (7.44% WER) than Parakeet v2 (6.05%); faster-whisper
helps speed, but the small/distilled models fast enough on CPU are
much less accurate. Whisper DOES punctuate, so Speaches would fix
punctuation at the cost of accuracy and speed — and its realtime API
is untested with Dictate's client. It also cannot do rewording:
it serves STT/TTS only, no /chat/completions — rewording needs an
LLM, which Speaches is not.

## Punctuation: it needs the PnC model (2026-11-16)

Nemotron installed and streaming, but NO punctuation: the log shows
"well so i changed some stuff but not the yaml" and "this is a single
sentence" -- raw lowercase everywhere. That is by design in the
NeMo-Speech.cpp runtime: the ASR model emits unpunctuated text, and
punctuation/capitalization is a SEPARATE BERT model (PnC) attached as
postprocessing (profanity -> ITN -> PnC, on the final transcript).
NVIDIA's "supports punctuation" means the pipeline supports it with
PnC attached, not that the 0.6B model emits it. The ready-made GGUF
is published by suneetk/nemo-speech-companions (NVIDIA's
punctuation_en_bert.nemo, converted, 139 MB) -- it is NOT in the
Nemotron repo itself.

  cd ~/streaming-dictation/models
  curl -L -o pnc-bert-base-en.q8_0.gguf \
    https://huggingface.co/suneetk/nemo-speech-companions/resolve/main/pnc-bert-base-en.q8_0.gguf
  # then add this line to models/nemotron-speech-streaming-en-0.6b.yaml
  # under options: (it is already in the YAML in this zip)
  #   - pnc_model:pnc-bert-base-en.q8_0.gguf
  cd ~/streaming-dictation && docker compose restart localai

What to check in wsproxy.log: do the "UPSTREAM completed:" lines
gain punctuation now? And the "UPSTREAM delta:" lines? Postprocessing
runs on the final transcript, so the completed text may punctuate
while the live deltas stay raw -- Dictate lands finalized turns from
the completeds, so most of the text should still come out punctuated.
If NEITHER gains punctuation, the backend may need the request-side
`enable_automatic_punctuation` flag as well -- report back and that
is the next thing to check in LocalAI's nemo backend code.

## The ~1 s pause after stop: log proves it, proxy now closes it (2026-11-16)

Kanon's Nemotron log is the cleanest evidence yet. Turns commit
promptly now (speech_stopped -> committed -> completed ~1 s later,
e.g. "this is a single sentence"), and the last completed landed at
19:07:21 -- but the stop-commit went out at 19:07:27 and the socket
closed at 19:07:28. Six seconds after the turn was done, the stop
still cost ~1 s. That is Dictate's tail wait (IDLE_MS = 1_200 ms)
restarting fresh at every stop: only a `completed` arriving AFTER
the stop (while Dictate is waiting) ends it early. A pre-stop
completed does not help -- which is why the 3-second wait test
changed nothing.

wsproxy.py now closes the session itself in exactly that case: it
watches turn state (a `completed` sets it; the next
`speech_started` or delta clears it) and, when a stop-commit
arrives with the turn already completed, sends a WS close frame at
once. Dictate sees the close, ends the tail wait immediately
(PROVIDER outcome) and commits the transcript it already has. When
the turn is still open the proxy does nothing and the tail wait
protects the last words, as before. Expected behaviour from here:
- stop AFTER a pause (turn already completed): text lands
  essentially instantly.
- stop RIGHT after speaking (turn still open): still ~1 s -- that
  is the tail wait letting the lagging last words arrive, correct
  behaviour, not a bug.
The one hole: speaking a word and stopping inside one VAD tick
(~300 ms) after a completed turn -- the new turn's speech_started
may not have arrived yet and the word fragment is lost. A stop
lands after a finished thought, not mid-word, and the proxy
re-checks the flag 50 ms after the commit before closing, so this
stays a corner case. If a last word ever goes missing right after a
pause, say so -- that is the first suspect.
Tests: test_wsproxy.py (12/12) and test_wsproxy_close.py (4/4:
close when completed, commit still forwarded, no close when open,
no close when a new turn started) both pass locally.

## Punctuation: the PnC model is attached but never runs (2026-11-16)

Verified in LocalAI's own source (clone in ~/workspace/localai-source):
the nemo backend's ABI has an `EnableAutomaticPunctuation` field
(backend/go/nemo-speech-cpp/abi.go:169), and the NeMo-Speech.cpp
runtime gates the PnC stage on the per-request
`enable_automatic_punctuation` flag -- but NOTHING in LocalAI ever
sets it. The live path (asr_stream.go openSession) sets only
LanguageCode, EnableWordTimeOffsets and EnableSpeakerDiarization;
the offline path (asr.go) doesn't set it either; and parseOptions
(backend/go/nemo-speech-cpp/options.go) has no YAML key for it.
So the PnC file can sit in ./models/ and the log can say pnc:true,
and punctuation still never happens. This is an upstream LocalAI
gap, not a config error: the fix is one line in the backend
(set EnableAutomaticPunctuation when pnc_model is configured).
The 139 MB file is harmless dead weight until then -- keep it, in
case a LocalAI update wires the flag up.

What DOES punctuate, today, verbatim:
- Pipeline A in this folder (models/dictate-streaming-tdt.yaml):
  Parakeet TDT v3 + server_vad. The model self-punctuates, so
  turns land WITH punctuation and capitals. Cost: text appears per
  turn at pauses, not word-by-word. Try it with
  WS_PROXY_MODEL=dictate-streaming-tdt when relaunching wsproxy.py.
  Dictate needs no change.
- The batch provider (odin :8816, parakeet-api): punctuates, but
  no live words at all.
What does NOT punctuate:
- Dictate's auto-formatting via the laptop 8B: its prompt only
  applies formatting the speaker explicitly asked for ("period",
  "question mark") and otherwise returns the transcript as-is --
  it adds a trailing period at most, not real punctuation.
- Dictate's rewording via the 8B: punctuates as a side effect, but
  rewrites the wording -- not verbatim dictation.
So the choice right now is live words (Nemotron, no punctuation)
or punctuation (TDT, per-turn text). If the live-words feel wins,
the remaining route to both is the LocalAI fix above.

## Close-on-commit: not yet tested (2026-11-16)

Kanon's 19:27 log is from the OLD wsproxy.py (no CLOSE-ON-COMMIT
and no "COMMIT with turn still open" lines; the session ended ~1 s
after the commit). The new proxy only needs the file swap:
pkill -f wsproxy.py, relaunch with the same command. Then a stop
after a pause should land instantly; a stop right after speaking
still takes ~1 s (turn still open, tail wait protecting the last
words).

## Cleanup 2026-11-16: nginx gone, debug logging off

- The nginx proxy (dictate-proxy, :8083) is REMOVED from
  docker-compose.yml -- wsproxy.py (:8084) superseded it, and
  nginx.conf is deleted from this folder. On odin, after unzipping
  this version over the folder: `docker compose down
  --remove-orphans` takes the old proxy container out (named
  volumes survive, so models/backends are untouched), then
  `docker compose up -d` brings LocalAI back alone. `rm nginx.conf`
  on odin too if you want it gone.
- The temporary `--log-level=debug` command is removed from the
  compose file -- logs are back to normal volume. The diagnosis it
  was for is done (turn commits work, the 1 s pause is Dictate's
  tail wait, close-on-commit handles it).
- Unzip the whole zip over ~/streaming-dictation with "replace all":
  your downloaded .gguf files in ./models/ are NOT in the zip, so
  they survive; the new/updated YAMLs, wsproxy.py, tests and README
  come in. Then restart the proxy with the pipeline you want:
  WS_PROXY_MODEL=dictate-streaming-nemotron (live words, no
  punctuation) or WS_PROXY_MODEL=dictate-streaming-tdt (punctuated
  turns, no live words).
