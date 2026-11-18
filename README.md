# minis-voice-relay

给 **Minis App** 补上语音输入 / 语音输出的本地中转，后端接 DeepSeek ASR / TTS。
*Give **Minis App** voice input and voice output — a local relay in front of DeepSeek ASR / TTS.*

[中文](#中文) · [English](#english)

```
MIT License · zero dependencies (Python stdlib + POSIX sh) · tested on iSH / Alpine aarch64
```

---

<div id="中文"></div>

# 中文

## 这是什么

Minis 的语音入口只能选一个「模型」，并要求该模型的 `modalities` 含 `audio_input` /
`audio_output`。而 DeepSeek 没有音频模型。于是这里跑一个本机 HTTP 中转，把自己伪装成
一个 OpenAI 兼容的音频服务商，把语音请求翻译成 DeepSeek 的 `tools/asr` 与 `tools/tts` 调用。

> 纯标准库 Python + POSIX sh，无第三方依赖。`curl` 用于探活，`ffmpeg` 可选（mp3 输出与格式转码）。

## 为什么需要它

| 需求 | Minis 期望 | DeepSeek 提供 |
|---|---|---|
| 语音输入 | `POST /v1/audio/transcriptions`（multipart） | `POST /v1/tools/asr`（仅 WAV/OGG，≤8 MiB，≤120 s） |
| 语音输出 | `POST /v1/audio/speech`（JSON，要 mp3/wav/opus） | `POST /v1/tools/tts`（wav/pcm，opus 仅 WS 裸包） |

协议、格式、鉴权都对不上。中转负责全部对齐：multipart 解析、音频转码、音色映射、
PCM 补 WAV 头、Opus 封 Ogg、mp3 本地转码。

## 安装

```sh
git clone https://github.com/OutoriNemuri/minis-voice-relay.git
cd minis-voice-relay

sh bin/autostart.sh save-key sk-xxxxxxxx   # 写入 ~/.config/minis-voice-relay/key（600）
sh bin/preflight.sh                        # relay: started / already running
sh bin/autostart.sh doctor                 # 期望 health: ok
```

**让 AI 助手装**：把仓库丢给它，让它读 [`AGENTS.md`](AGENTS.md)（英文版 [`AGENTS.en.md`](AGENTS.en.md)），
里面有逐步操作、验收标准和「已被否决的自启方案」清单。

## 接入 Minis

1. **设置 → 模型服务商 → 添加**：类型 `OpenAI 兼容`，Base URL `http://127.0.0.1:8765`，API Key 随便填非空值。
2. 在该服务商下建两个模型：

   | 模型名 | 模态 | 用途 |
   |---|---|---|
   | `deepseek-asr-relay` | `text_input` `text_output` `audio_input` | 语音输入 |
   | `deepseek-tts-relay` | `text_input` `audio_output` | 语音输出 |

3. **设置 → 模型分组**：
   - 「语音输入」组：`deepseek-asr-relay` 第一位，后面接文本模型（如 `deepseek-flash`）
   - 「语音输出」组：内置 `system-tts`（免费离线）优先，`deepseek-tts-relay` 兜底

## 端点

| 方法 | 路径 | 行为 |
|---|---|---|
| `POST` | `/v1/audio/transcriptions` | multipart `file` → 规范化音频 → DeepSeek ASR → `{"text": "…"}` |
| `POST` | `/v1/audio/speech` | `input`/`text`、`voice`、`response_format` → DeepSeek TTS → 音频字节 |
| `POST` | `/v1/chat/completions` | 无音频 → **400 `relay_no_audio`** |
| `GET` | `/health` | `{"ok": true}` |

**音色**：`stella`（女声，默认）/ `tide`（男声）。优先级：模型名后缀（`…-tide`）> 请求里的
`voice`/`voice_id` > `config.json` > `stella`。OpenAI 名字也认：`alloy/nova/fable/shimmer/coral`
→ stella，`echo/onyx/sage` → tide。

**格式**：`wav` 原样、`mp3` 本地 ffmpeg 转、`opus` → Ogg、`pcm` 补 WAV 头、`aac`/`flac` → wav。

## 一个刻意的设计：这个端点不聊天

`/v1/chat/completions` 收到不带音频的请求会**直接 400**，而不是转发给文本模型。
早期版本会转发，结果这个「ASR 模型」变成了通用聊天入口——切过去就能跟一个本该只做转写的模型聊天，
非常费解。语音服务就该只做语音。

## 自启的真相

**iOS 上不存在真正的开机自启。** Minis 打开 App 时不会执行任何 shell 脚本；shell 进程只在真正跑命令时才存在。

| 方案 | 结果 |
|---|---|
| Shortcuts「打开 App」自动化 | 每次切前后台触发一轮 Agent 对话，烧 token ❌ |
| `/usr/local/bin/minis-shell-init` | Minis 不会主动调用 ❌ |
| `~/.ashrc` 钩子 | App 启动会重置该文件 ❌ |
| `/etc/profile.d/*.sh` | 机制有效，但仅在 login shell 时触发（`autostart.sh install` 可选安装） |
| **会话首次 shell 调用跑 `preflight.sh`** | 0.02 s、零 token ✅ |

所以：**用语音前跑一次 `sh bin/preflight.sh`**。可以交给 AI 助手在会话第一次调用 shell 时执行。

## 运维

```sh
sh bin/relay.sh start|stop|status|log [行数]
sh bin/autostart.sh doctor
sh bin/autostart.sh voice stella|tide      # 默认音色（不带参数=查看）
sh bin/watchdog.sh                         # 一声 curl，约 5 ms
```

端口 `RELAY_PORT`（默认 8765）、日志 `RELAY_LOG`（默认 `/tmp/mvrelay.log`）均可覆盖。

## 计费与限制

ASR ¥0.78/小时、TTS ¥3/万字符（汉字算 2 字符）。两个端点**都不自动重试**——中途断流重试会重复计费。
ASR 单次 ≤120 秒、≤8 MiB；并发上限 50。

## 许可

MIT，见 [LICENSE](LICENSE)。

---

<div id="english"></div>

# English

## What it is

Minis lets you pick a *model* for voice input/output, and requires that model to declare the
`audio_input` / `audio_output` modalities. DeepSeek ships no audio model. So this project runs a tiny
local HTTP relay that poses as an OpenAI-compatible audio provider and translates requests into
DeepSeek's `tools/asr` and `tools/tts` calls.

> Python standard library + POSIX sh, no third-party dependencies. `curl` for health checks,
> `ffmpeg` optional (mp3 output and format transcoding).

## Why

| Need | Minis speaks | DeepSeek speaks |
|---|---|---|
| Voice input | `POST /v1/audio/transcriptions` (multipart) | `POST /v1/tools/asr` (WAV/OGG only, ≤8 MiB, ≤120 s) |
| Voice output | `POST /v1/audio/speech` (JSON, wants mp3/wav/opus) | `POST /v1/tools/tts` (wav/pcm; opus only as raw WS frames) |

The protocols don't line up at all. The relay bridges everything: multipart parsing, audio
transcoding, voice mapping, WAV header synthesis for raw PCM, Ogg encapsulation, local mp3 encoding.

## Install

```sh
git clone https://github.com/OutoriNemuri/minis-voice-relay.git
cd minis-voice-relay

sh bin/autostart.sh save-key sk-xxxxxxxx   # writes ~/.config/minis-voice-relay/key (600)
sh bin/preflight.sh                        # relay: started / already running
sh bin/autostart.sh doctor                 # expect health: ok
```

**Let an AI install it**: hand it the repo and point it at [`AGENTS.en.md`](AGENTS.en.md)
(Chinese: [`AGENTS.md`](AGENTS.md)). It contains step-by-step instructions, the definition of done,
and the list of autostart approaches that were tested and rejected.

## Wiring into Minis

1. **Settings → Providers → Add**: type `OpenAI compatible`, Base URL `http://127.0.0.1:8765`,
   API key any non-empty value.
2. Add two models under it:

   | Model | Modalities | Purpose |
   |---|---|---|
   | `deepseek-asr-relay` | `text_input` `text_output` `audio_input` | voice input |
   | `deepseek-tts-relay` | `text_input` `audio_output` | voice output |

3. **Settings → Model Groups**:
   - "Voice input": `deepseek-asr-relay` first, then your text model (e.g. `deepseek-flash`)
   - "Voice output": built-in `system-tts` (free, offline) first, `deepseek-tts-relay` as fallback

## Endpoints

| Method | Path | Behaviour |
|---|---|---|
| `POST` | `/v1/audio/transcriptions` | multipart `file` → normalise → DeepSeek ASR → `{"text": "…"}` |
| `POST` | `/v1/audio/speech` | `input`/`text`, `voice`, `response_format` → DeepSeek TTS → audio bytes |
| `POST` | `/v1/chat/completions` | no audio → **400 `relay_no_audio`** |
| `GET` | `/health` | `{"ok": true}` |

**Voices**: `stella` (female, default) / `tide` (male). Precedence: model-name suffix (`…-tide`) >
`voice`/`voice_id` in the body > `config.json` > `stella`. OpenAI names are accepted too:
`alloy/nova/fable/shimmer/coral` → stella, `echo/onyx/sage` → tide.

**Formats**: `wav` passthrough, `mp3` via local ffmpeg, `opus` → Ogg, `pcm` gets a WAV header,
`aac`/`flac` → wav.

## A deliberate design choice: this endpoint does not chat

`/v1/chat/completions` **rejects audio-free requests with 400** instead of forwarding them to a text
model. An earlier version forwarded them, which turned the "ASR model" into a general chat entry —
you could switch to it and converse with a model that was supposed to only transcribe. A voice
service should only do voice.

## The truth about autostart

**There is no real boot-time autostart on iOS.** Minis does not execute any shell script when the app
opens; a shell only exists while a command actually runs.

| Approach | Outcome |
|---|---|
| Shortcuts "When App is Opened" | fires an agent turn on every foreground switch — burns tokens ❌ |
| `/usr/local/bin/minis-shell-init` | Minis never invokes it ❌ |
| `~/.ashrc` hook | the app resets that file on launch ❌ |
| `/etc/profile.d/*.sh` | works, but only for login shells (`autostart.sh install`, optional) |
| **`preflight.sh` at the first shell call of a session** | 0.02 s, zero tokens ✅ |

So: **run `sh bin/preflight.sh` before using voice.** An AI assistant can do it at the start of a session.

## Operations

```sh
sh bin/relay.sh start|stop|status|log [lines]
sh bin/autostart.sh doctor
sh bin/autostart.sh voice stella|tide      # default voice (no arg = show)
sh bin/watchdog.sh                         # one curl, ~5 ms
```

Override `RELAY_PORT` (default 8765) and `RELAY_LOG` (default `/tmp/mvrelay.log`).

## Cost & limits

ASR ¥0.78/hour, TTS ¥3 per 10k characters (a CJK character counts as 2). **No automatic retries** on
either endpoint — retrying after a mid-stream drop is billed twice. ASR: ≤120 s, ≤8 MiB per request;
concurrency cap 50.

## License

MIT — see [LICENSE](LICENSE).
