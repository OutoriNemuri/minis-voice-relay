# AGENTS.md (English)

> An install manual for AI assistants. After cloning this repo, **just follow the steps below.
> Don't ask the user anything technical** — only step 2 (the API key) needs their input.

Chinese version: [AGENTS.md](AGENTS.md)

---

## 0. What you are doing

Install and run `minis-voice-relay` so that **Minis App (iOS)** gains voice input and voice output.
Done when `sh bin/autostart.sh doctor` reports `health: ok` and Minis can speak.

Read `README.md` first. Voice selection is optional — the default `stella` is fine.

## 1. Check the environment

```sh
python3 --version     # 3.9+, standard library only
command -v curl       # used by the scripts for health checks
command -v ffmpeg     # optional but strongly recommended (mp3 output, format conversion)
```

Install what's missing (`apk add python3 curl ffmpeg`, `apt-get install -y python3 curl ffmpeg`).
**Do not add any third-party Python package** — this project is deliberately dependency-free.

## 2. Get an API key (the only step needing the user)

You need a DeepSeek API key (`sk-...`) for the ASR / TTS upstream.

- If `DEEPSEEK_API_KEY` is already set, use it.
- Otherwise **ask the user**, then persist it immediately:

```sh
sh bin/autostart.sh save-key sk-the-key
```

This writes `~/.config/minis-voice-relay/key` with mode 600.
**Never** put the key in the repo, README, logs, or commit history.

## 3. Start and self-test

```sh
sh bin/preflight.sh
#   relay: started            ← first start, ~0.3–0.8 s
#   relay: already running    ← already up

sh bin/autostart.sh doctor
# expect: health: ok
```

Troubleshooting:

| Symptom | Fix |
|---|---|
| `缺少 DEEPSEEK_API_KEY` / missing key | back to step 2 |
| `启动超时` (start timeout) | `sh bin/relay.sh log 50` |
| Port already in use | `RELAY_PORT=8766 sh bin/preflight.sh`, and use the same var everywhere |
| `curl: (7) Failed to connect` | service didn't start — read the log |

Verify the relay on its own:

```sh
curl -s http://127.0.0.1:8765/health      # {"ok": true}

curl -s -X POST http://127.0.0.1:8765/v1/audio/speech \
     -H 'Content-Type: application/json' \
     -d '{"input":"Installed.","response_format":"mp3"}' -o /tmp/ok.mp3
ls -l /tmp/ok.mp3                          # should be several KB or more

ffmpeg -f lavfi -i "sine=frequency=440:duration=1" -ar 16000 -ac 1 /tmp/t.wav
curl -s -X POST http://127.0.0.1:8765/v1/audio/transcriptions -F "file=@/tmp/t.wav"
# {"text": "..."} — 200 is the pass condition (a pure tone may transcribe as empty)
```

## 4. Wire it into Minis

**UI route (recommended):**

1. Minis → **Settings → Providers → Add**
   - Type: `OpenAI compatible`
   - Base URL: `http://127.0.0.1:8765`
   - API key: any non-empty value (the relay doesn't check it)
2. Add two models under that provider:

   | Model | Modalities | Purpose |
   |---|---|---|
   | `deepseek-asr-relay` | `text_input` `text_output` `audio_input` | voice input |
   | `deepseek-tts-relay` | `text_input` `audio_output` | voice output |

3. **Settings → Model Groups**:
   - "Voice input" group: `deepseek-asr-relay` **first**, then your text model (e.g. `deepseek-flash`)
   - "Voice output" group: built-in `system-tts` (local, free) first, `deepseek-tts-relay` as fallback

CLI route is documented in `docs/architecture.md`. `minis-config` uses snake_case fields
(`instance_id`, `model_id`); camelCase fails with `invalid_value: instance_id required`.

> ⚠️ The built-in entry `__builtin_system_speech__/system-tts` is not in the `models` list and
> **cannot be written back via `minis-config set groups.<id>.entries`**. Overwriting the group drops
> it — the user has to drag it back in the UI.

## 5. Autostart (most commonly botched step — don't improvise)

**There is no real boot-time autostart on iOS.** Minis does not execute any shell script when the app
opens; the shell only exists while a command actually runs. These approaches were tested and rejected:

| ❌ Attempt | Why it fails |
|---|---|
| Shortcuts "When App is Opened" automation | fires a full agent turn on every foreground switch — burns tokens |
| `/usr/local/bin/minis-shell-init` | Minis never invokes it |
| `~/.ashrc` hook | the app resets that file on launch |
| background `crontab` / `nohup` daemon | dies when the app is suspended |

**Do this instead**: run `bin/preflight.sh` at the first shell call of a session (0.02 s, zero tokens).
If you are an AI assistant, just run it once when you first use the shell in a conversation.
Optional extra: `sh bin/autostart.sh install` writes an `/etc/profile.d` hook (login shells only).

## 6. Day-to-day commands

```sh
sh bin/relay.sh start|stop|status|log [lines]
sh bin/autostart.sh doctor                    # full health check
sh bin/autostart.sh voice stella|tide         # default voice (no arg = show)
sh bin/autostart.sh save-key sk-...           # rotate the key
sh bin/watchdog.sh                            # one curl, safe to hook anywhere
```

Log: `/tmp/mvrelay.log` (`RELAY_LOG=...` to change). Port: 8765 (`RELAY_PORT=...` to change —
remember to update the Base URL in Minis too).

## 7. Never do these

- ❌ Don't use this relay as a general-purpose chat API. `/v1/chat/completions` returns
  **400 `relay_no_audio`** for audio-free requests — that's deliberate (an earlier version forwarded
  them to a text model, which made the "ASR model" answer chat messages, confusingly).
- ❌ Don't add automatic retries to TTS/ASR calls. A retry after a mid-stream drop is **billed twice**.
- ❌ Don't print the contents of `~/.config/minis-voice-relay/key` into logs, chat, or commits.
- ❌ Don't install a system daemon or tweak app launch settings to keep the service alive — impossible
  on iOS and it slows every startup.
- ❌ Don't fiddle with the voice unless asked. Default `stella` is fine.

## 8. Definition of done

Report to the user, in one message:

1. the `health: ok` line from `doctor`;
2. the size of the self-test `/tmp/ok.mp3`;
3. the two things they must confirm by hand in Minis (provider Base URL, model group ordering);
4. a clear note that **the relay stops when the app is reopened — run `sh bin/preflight.sh` before
   using voice** (or let the assistant run it at the start of a session).

## 9. Quick troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Minis shows "request failed (HTTP 404)" + a DeepSeek request_id | the request never reached the relay | check the Base URL is `http://127.0.0.1:8765`, confirm `/health` |
| Minis shows "request failed (HTTP 400)" | text-only request, no audio | expected — see §7 item 1 |
| Still a female voice after switching | upstream only honours `voice_id`; older relays sent `voice` | use this repo, and a model name ending in `-tide` |
| mp3 output fails | ffmpeg missing | install ffmpeg |
| "audio not compliant" | wrong format (16-bit PCM WAV or Ogg Opus only, ≤8 MiB, ≤120 s) | install ffmpeg so the relay transcodes |
| audio longer than 120 s | hard upstream limit | the relay truncates to 120 s with ffmpeg |
