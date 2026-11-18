# 架构与实测记录（Architecture & Field Notes）

> 这一层**不属于 DeepSeek API 的对齐范围**，是为 Minis App 的「语音输入」要求"模型必须支持音频"而加的本地适配器。
> 上游统一是 `POST https://api.deepseek.com/v1/tools/asr` / `.../tts`。

## 问题

Minis 的语音输入只能选「模型」，且该模型的 `modalities` 必须含 `audio_input`。
DeepSeek 没有音频输入模型，于是无论选哪个都走不通。

## 方案

把语音输入指向本机的一个小服务，它把音频改道到 DeepSeek ASR：

```
麦克风 → Minis「语音输入」组 → ASR provider (http://127.0.0.1:8765)
      → bin/relay.py：multipart 解析 → ffmpeg 16k 单声道
      → api.deepseek.com/v1/tools/asr → {"text": …}
      → 回 {"text": "…"} → Minis 填进输入框
```

## 实测记录（2026-11-15）

| 环节 | 观察 |
|---|---|
| Minis 实际请求 | `POST /v1/audio/transcriptions`，`multipart/form-data; boundary=VoiceBoundary-…`，UA `Minis/16 CFNetwork/…` |
| 首次失败 | 中转只实现了 `/v1/chat/completions`，未知路径落到转发逻辑 → DeepSeek 后端 `404 Not Found`，App 显示「请求失败（HTTP 404）」加 DeepSeek 的 request_id |
| 修复 | 增加 `/v1/audio/transcriptions` 路由：解析 multipart 的 `file` 字段 → ASR → 返回 `{"text": …}` |
| 结果 | 3 秒音频 2.5 秒返回，转写与口述一字不差；m4a / wav 都直接可用 |
| 计费 | 3 秒约 ¥0.00065（限时 5 折 ¥0.0003） |

关键结论：**Minis 的语音输入用的是 STT 专用端点**（不是 Chat Completions 的 `input_audio`），
所以不需要给模型伪造 `audio_input` 也能跑——但它的 UI 仍然要求选中一个"支持音频"的模型条目。

## 配置步骤

1. 启动中转（需要 `DEEPSEEK_API_KEY`）：
   ```sh
   sh bin/relay.sh start      # status / stop / log
   ```
2. [Providers](minis://settings/providers) 添加：类型 `OpenAI 兼容`，Base URL `http://127.0.0.1:8765`，Key 任意值。
3. 添加模型 `deepseek-asr-relay`，把模态设为 `["text_input","text_output","audio_input"]`：
   ```sh
   minis-config set models.<条目ID>.modalitiesOverride '["text_input","text_output","audio_input"]'
   ```
4. 放进「语音输入」组（字符串数组 + 逗号分隔），并把它排在第一位：
   ```sh
   minis-config set groups.<语音输入组ID>.entries \
     '["<provider实例ID>/deepseek-asr-relay","<你的文本模型条目>"]'
   ```

## 自动拉起（实测结论）

**iOS 上 Minis 打开时不会执行任何 shell 脚本。** 实测：停掉中转后真跑一次
`sh -lc 'echo login-ok'`，3 秒内 `/health` 变 200 —— 说明 `profile.d` 钩子本身有效，
但触发点是「真的执行一次命令」，而不是「打开 App」。所以不存在真正的开机自启，
能做的只有让拉起成本降到最低：

```sh
sh bin/preflight.sh
# 已在跑 → "relay: already running"（0.02s，零 token）
# 没在跑 → "relay: started"（0.7s，起完就返回）
```

密钥来源：环境变量 → `~/.config/minis-voice-relay/key`（`sh bin/autostart.sh save-key sk-…`）。
保存 key：`sh autostart.sh save-key sk-...`（写入 600 权限文件）。

**已否决的方案**：Shortcuts「打开 App」自动化 —— 每次切前后台都会触发一轮 Agent 对话，烧 token；
`/usr/local/bin/minis-shell-init` —— Minis 不会主动调用它（已删除）。

## 已知限制

- **进程会随 App 挂起而停**：用前先 `relay.sh start`；要常驻需 App 的后台模式或 Apple Shortcuts 自动化。
- DeepSeek ASR：≤120 秒、≤8 MiB、仅 16 位 WAV / Ogg Opus（中转内部已统一转码）。
- 中转不校验 Authorization，但会用自己的 `DEEPSEEK_API_KEY` 调上游；密钥不会出现在 App 配置里。
- 失败时 App 会重试；中转对每次请求独立转写，不会去重。

## 语音输出（TTS）同样可以兼容——已实现

Minis 的「语音输出」路线与语音输入同构：选中模型 → 调专用端点。DeepSeek 没有
`/v1/audio/speech`（实测 404 带 request_id），但中转新增了对等分支：

```
Minis「朗读回复」→ POST /v1/audio/speech {model, input, voice, response_format}
                → bin/relay.py：input→text、voice 映射、format 映射
                → api.deepseek.com/v1/tools/tts
                → 回二进制音频（audio/wav 或 audio/mpeg）
```

字段对照：

| Minis（OpenAI 风格） | DeepSeek | 说明 |
|---|---|---|
| `input`（也接受 `text`） | `text` | 空字符串直接 400 |
| `voice` | `voice_id` | `alloy/nova/fable/shimmer/coral→stella`，`echo/onyx/sage→tide`；未知值回落 stella |
| `response_format: wav` | `format: wav` | 原样返回（24 kHz 16 位单声道） |
| `response_format: mp3` | 上游 wav → 本地 ffmpeg 转 `libmp3lame 64k` | 返回 `audio/mpeg` |
| `response_format: opus` | 本地转 ogg | 返回 `audio/ogg` |
| `response_format: pcm` | `format: pcm` | 本地补 44 字节 WAV 头后返回 wav |

实测（2026-11-15）：

```
POST /v1/audio/speech {input:"语音输出兼容测试。", voice:"tide", response_format:"mp3"}
→ 200 audio/mpeg 18,285 B → ffprobe: mp3 / 2.26 s

POST /v1/audio/speech {input:"你好，这是 wav。", voice:"alloy", response_format:"wav"}
→ 200 audio/wav 78,764 B → pcm_s16le / 1.64 s
```

**推荐顺序**：本地优先，中转兜底。

```
语音输出：system-tts（内置，本地免费） → deepseek-tts-relay（DeepSeek，按字符计费）
语音输入：deepseek-asr-relay → DeepSeek-V4.1-Flash
```

注意：内置条目 `__builtin_system_speech__/system-tts` **无法通过 minis-config 写入组**
（`invalid_value: Unknown model entry uuid`），它不在 `models` 列表里，只能在上面的界面里拖。
用 `minis-config set groups.<id>.entries` 覆盖该组时会把内置条目一并清掉，改完记得人工拖回来。

配置记录：复用同一个 provider（`http://127.0.0.1:8765`），新增模型 `deepseek-tts-relay`
（模态 `["text_input","audio_output"]`），置于「语音输出」组首位：

```sh
minis-config add models '{"instance_id":"<provider实例ID>","model_id":"deepseek-tts-relay","display_name":"DeepSeek TTS Relay","modalitiesOverride":["text_input","audio_output"]}'
minis-config set groups.<语音输出组ID>.entries \
  '["<provider实例ID>/deepseek-tts-relay","<你的文本模型条目>"]'
```

注意 `models add` 的字段名是 **`instance_id` / `model_id`**（snake_case），
首次用 `providerInstanceId` 会报 `invalid_value: instance_id required`。

DeepSeek 只有 `stella` / `tide` 两个音色，没有克隆、没有语速调节。

注意：TTS 按计费字符收费（汉字算 2 字符，¥3/万字符）。朗读整段回答前建议先算一下费用。
