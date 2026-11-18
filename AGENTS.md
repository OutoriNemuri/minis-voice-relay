# AGENTS.md

> 给 AI 助手的安装手册。克隆本仓库后，**照着下面的步骤做完即可，不需要问用户任何技术细节**——
> 只有第 2 步（API Key）需要用户提供，其余都可自主完成。

本文件同时提供英文版：[AGENTS.en.md](AGENTS.en.md)

---

## 0. 你要做的事

把 `minis-voice-relay` 装好并跑起来，让 **Minis App（iOS）** 能用上语音输入 / 语音输出。
装完的标志：`sh bin/autostart.sh doctor` 里 `health: ok`，且 Minis 里能听到合成的语音。

**先读一遍 `README.md`**，尤其是「怎么选音色」之外的部分。音色不是必选项，默认 `stella`。

## 1. 环境检查

```sh
python3 --version     # 需要 3.9+，只用标准库
command -v curl       # 脚本探活用
command -v ffmpeg     # 可选但强烈建议：mp3 输出、非 WAV/OGG 输入靠它
```

缺什么就装什么（Alpine/iSH：`apk add python3 curl ffmpeg`；Debian/Ubuntu：`apt-get install -y python3 curl ffmpeg`）。
**不要引入任何第三方 Python 包**，这个项目刻意零依赖。

## 2. 取得 API Key（唯一需要用户参与的一步）

需要用户提供一个 DeepSeek API Key（`sk-...`），用来调用 DeepSeek 的 ASR / TTS。

- 环境里已有 `DEEPSEEK_API_KEY` 就直接用；
- 否则**问用户要**，拿到后立即落盘，不要写进任何会被提交的文件：

```sh
sh bin/autostart.sh save-key sk-用户给的key
```

它会写入 `~/.config/minis-voice-relay/key`（权限 600）。
**绝对不要**把密钥写进仓库、README、日志或提交历史。

## 3. 启动并自检

```sh
sh bin/preflight.sh
# 期望输出之一：
#   relay: started            ← 首次启动，约 0.3–0.8 秒
#   relay: already running    ← 已在运行

sh bin/autostart.sh doctor
# 期望：health: ok
```

失败的排查顺序：

| 现象 | 处理 |
|---|---|
| `缺少 DEEPSEEK_API_KEY` | 回到第 2 步 |
| `启动超时，看 /tmp/mvrelay.log` | `sh bin/relay.sh log 50` 看报错 |
| 端口被占 | `RELAY_PORT=8766 sh bin/preflight.sh`，后续所有命令也带上同一变量 |
| `curl: (7) Failed to connect` | 服务没起来，看日志；确认不是被防火墙拦 |

独立验证中转本身（不经 Minis）：

```sh
curl -s http://127.0.0.1:8765/health
# {"ok": true}

# TTS：应当生成一个能播的 mp3
curl -s -X POST http://127.0.0.1:8765/v1/audio/speech \
     -H 'Content-Type: application/json' \
     -d '{"input":"安装成功。","response_format":"mp3"}' -o /tmp/ok.mp3
ls -l /tmp/ok.mp3    # 应有几千字节以上

# ASR：先造一段 16k 单声道 wav 再转写
ffmpeg -f lavfi -i "sine=frequency=440:duration=1" -ar 16000 -ac 1 /tmp/t.wav
curl -s -X POST http://127.0.0.1:8765/v1/audio/transcriptions -F "file=@/tmp/t.wav"
# 返回 {"text": "..."}（纯音调可能是空串，200 即算通）
```

## 4. 接入 Minis（需要懂 Minis 的两条路径）

**界面方式（推荐，用户自己点两下就好）**：

1. Minis → **设置 → 模型服务商 → 添加**
   - 类型：`OpenAI 兼容`
   - Base URL：`http://127.0.0.1:8765`
   - API Key：随便填非空值（中转不校验）
2. 在该服务商下建两个模型：

   | 模型名 | 模态勾选 | 用途 |
   |---|---|---|
   | `deepseek-asr-relay` | `text_input` `text_output` `audio_input` | 语音输入 |
   | `deepseek-tts-relay` | `text_input` `audio_output` | 语音输出 |

3. **设置 → 模型分组**：
   - 「语音输入」组：`deepseek-asr-relay` 放**第一位**，后面接你的文本模型（如 `deepseek-flash`）
   - 「语音输出」组：内置 `system-tts`（本地免费）优先，`deepseek-tts-relay` 兜底

命令行方式见 `docs/architecture.md` 的配置记录（需要 `minis-config`，字段是 snake_case：
`instance_id` / `model_id`，用驼峰会报 `invalid_value: instance_id required`）。

> ⚠️ 内置条目 `__builtin_system_speech__/system-tts` 不在 `models` 列表里，**无法用
> `minis-config set groups.<id>.entries` 写入**。覆盖该组会把内置条目清掉，只能让用户在界面上拖回来。

## 5. 自启（这是最容易做错的一步，别自由发挥）

**iOS 上不存在真正的开机自启**：Minis 打开 App 时不会执行任何 shell 脚本，shell 进程只在
真正跑命令时才存在。以下方案**已被实测否决，不要重试**：

| ❌ 方案 | 为什么不行 |
|---|---|
| Shortcuts「打开 App」自动化 | 每次切前后台触发一轮 Agent 对话，烧 token |
| `/usr/local/bin/minis-shell-init` | Minis 不会主动调用它 |
| `~/.ashrc` 钩子 | App 启动会重置该文件 |
| 后台 `crontab` / `nohup` 守护 | App 挂起后一并停止 |

**正确做法**：把 `bin/preflight.sh` 挂到「会话里第一次执行 shell」这个时机上（0.02 s、零 token）。
如果你是 AI 助手，就在本会话第一次调用 shell 时顺手跑一次它。
可选补充：`sh bin/autostart.sh install` 会写一个 `/etc/profile.d` 钩子（login shell 时拉起）。

## 6. 日常运维命令

```sh
sh bin/relay.sh start|stop|status|log [行数]
sh bin/autostart.sh doctor                    # 全面体检
sh bin/autostart.sh voice stella|tide          # 默认音色（不带参数=查看）
sh bin/autostart.sh save-key sk-...            # 换密钥
sh bin/watchdog.sh                             # 一声 curl，适合塞进任何钩子
```

日志默认 `/tmp/mvrelay.log`，可用 `RELAY_LOG=/path sh bin/preflight.sh` 改。
端口默认 8765，用 `RELAY_PORT=8766` 改（改完 Minis 里的 Base URL 也要跟着改）。

## 7. 绝对不要做的事

- ❌ 不要把这个中转当成通用聊天 API 去用。`/v1/chat/completions` 收到无音频请求会返回
  **400 `relay_no_audio`**，这是刻意的设计（早期版本会转发给文本模型，导致「ASR 模型能聊天」，
  行为非常费解）。
- ❌ 不要让 TTS / ASR 请求带自动重试。DeepSeek 中途断流重试会**重复计费**。
- ❌ 不要把 `~/.config/minis-voice-relay/key` 的内容打印到日志、对话或提交里。
- ❌ 不要为了「让服务常驻」而去改 App 的启动配置或装系统级 daemon，iOS 上做不到，还会拖慢每次启动。
- ❌ 不要在用户没要求的情况下把音色改来改去；默认 `stella` 就好。

## 8. 交付标准

装完请一次性向用户汇报：

1. `doctor` 的输出（`health: ok` 那一行）；
2. TTS 自检生成的 `/tmp/ok.mp3` 大小；
3. 提醒用户在 Minis 里需要手动确认的两步（服务商 Base URL、两个模型的分组顺序）；
4. 明确说明：**App 重新打开后中转会停，用语音前跑一次 `sh bin/preflight.sh`**（或让 AI 助手在会话开始时跑）。

## 9. 排障速查

| 症状 | 原因 | 处理 |
|---|---|---|
| Minis 显示「请求失败（HTTP 404）」+ DeepSeek request_id | 请求没进中转 | 确认 Base URL 是 `http://127.0.0.1:8765`，`/health` 通 |
| Minis 显示「请求失败（HTTP 400）」 | 发的是纯文本，没有音频 | 正常，见第 7 节第 1 条 |
| 合成的是女声，想换男声却没变 | 上游只认 `voice_id`；旧版中转发的是 `voice` | 确认用的是本仓库版本；用 `-tide` 结尾的模型名 |
| mp3 生成失败 | 缺 ffmpeg | `apk add ffmpeg` / `apt-get install ffmpeg` |
| 转写报「音频不合规」 | 格式不对（只收 16 位 PCM WAV / Ogg Opus，≤8 MiB，≤120 秒） | 装 ffmpeg 让中转自动转码 |
| 音频超过 120 秒 | 上游硬限制 | 中转会用 ffmpeg 截到 120 秒 |
