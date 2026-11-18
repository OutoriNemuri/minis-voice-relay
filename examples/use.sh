#!/bin/sh
# 常见用法示例（复制改改就能用）
set -e
R="$(cd "$(dirname "$0")/.." && pwd)"
P=http://127.0.0.1:8765

# —— 0. 起服务（幂等，随时可跑）——
sh "$R/bin/preflight.sh"

# —— 1. 转写一段音频 ——
# ASR 只吃 16 位 PCM WAV 或 Ogg Opus，≤8 MiB、≤120 秒；其余格式先转：
ffmpeg -hide_banner -loglevel error -y -i input.m4a -t 120 -ac 1 -ar 16000 \
       -c:a pcm_s16le /tmp/in16k.wav
curl -sS -X POST "$P/v1/audio/transcriptions" \
     -F "file=@/tmp/in16k.wav"

# —— 2. 合成语音（Minis 的请求格式）——
curl -sS -X POST "$P/v1/audio/speech" \
     -H 'Content-Type: application/json' \
     -d '{"input":"这段话会变成人声。","voice":"stella","response_format":"mp3"}' \
     -o /tmp/out.mp3

# —— 3. 直接调 DeepSeek 原生 TTS（不经中转，拿 PCM 裸流）——
# curl -sS -X POST https://api.deepseek.com/v1/tools/tts \
#      -H "Authorization: Bearer $DEEPSEEK_API_KEY" -H 'Content-Type: application/json' \
#      -d '{"text":"你好","voice":"tide","format":"pcm"}' -o /tmp/out.pcm
