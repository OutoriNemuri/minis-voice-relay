#!/bin/sh
# minis-voice-relay 自检：不依赖 Minis、不依赖 Agent，纯本地跑一遍。
#   sh bin/doctor.sh
# 退出码 0 = 全绿；非 0 = 有环节挂了（哪一环看输出）。
HERE="$(cd "$(dirname "$0")" && pwd)"
PORT="${RELAY_PORT:-8765}"
BASE="http://127.0.0.1:$PORT"
FAIL=0
ok()   { printf '  \033[32mok\033[0m   %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
note() { printf '       %s\n' "$1"; }

echo "== 1. 依赖 =="
python3 -c 'import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)' \
  && ok "python3 $(python3 -V 2>&1 | cut -d' ' -f2)" || bad "python3 版本过低（需 3.9+）"
command -v curl >/dev/null && ok "curl" || bad "curl 缺失（脚本探活要用）"
if command -v ffmpeg >/dev/null; then ok "ffmpeg"; else note "ffmpeg 缺失：mp3 输出与非 WAV/OGG 输入会失败"; fi

echo "== 2. 密钥 =="
if [ -n "$DEEPSEEK_API_KEY" ]; then
  ok "环境变量 DEEPSEEK_API_KEY（长度 ${#DEEPSEEK_API_KEY}）"
elif [ -r "$HOME/.config/minis-voice-relay/key" ]; then
  ok "~/.config/minis-voice-relay/key"
else
  bad "没有密钥 → sh bin/autostart.sh save-key sk-..."
fi

echo "== 3. 服务 =="
if curl -sf -m 2 "$BASE/health" >/dev/null 2>&1; then
  ok "$BASE/health"
else
  bad "未响应 → 先跑 sh bin/preflight.sh"
  note "预检失败就去看日志：sh bin/relay.sh log 40"
  echo; echo "结果：$FAIL 项失败"; exit 1
fi

echo "== 4. 合成（TTS） =="
OUT=/tmp/mvrelay_doctor.mp3
CODE=$(curl -sS -m 60 -o "$OUT" -w '%{http_code}' -X POST "$BASE/v1/audio/speech" \
  -H 'Content-Type: application/json' \
  -d '{"input":"自检通过。","response_format":"mp3"}' 2>/dev/null)
SIZE=$(wc -c <"$OUT" 2>/dev/null || echo 0)
if [ "$CODE" = "200" ] && [ "$SIZE" -gt 2000 ]; then
  ok "mp3 $SIZE 字节"
else
  bad "TTS 返回 $CODE，$SIZE 字节"
fi

echo "== 5. 转写（ASR） =="
# 用纯 Python 生成 1 秒 440 Hz WAV：不依赖 ffmpeg 是否带 lavfi（iSH 上的 ffmpeg 就没有）
python3 - <<'PY'
import struct, math
rate, n = 16000, 16000
data = b"".join(struct.pack("<h", int(8000*math.sin(2*math.pi*440*i/rate))) for i in range(n))
hdr = b"RIFF" + struct.pack("<I", 36+len(data)) + b"WAVEfmt " + \
      struct.pack("<IHHIIHH", 16, 1, 1, rate, rate*2, 2, 16) + b"data" + struct.pack("<I", len(data))
open("/tmp/mvrelay_doctor.wav","wb").write(hdr+data)
PY
CODE=$(curl -sS -m 120 -X POST "$BASE/v1/audio/transcriptions" \
  -F "file=@/tmp/mvrelay_doctor.wav" -o /tmp/mvrelay_doctor.json \
  -w '%{http_code}' 2>/dev/null)
BODY=$(head -c 200 /tmp/mvrelay_doctor.json 2>/dev/null)
if [ "$CODE" = "200" ]; then
  ok "200 $(printf '%s' "$BODY" | head -c 120)"
else
  bad "ASR 返回 $CODE $(printf '%s' "$BODY" | head -c 120)"
fi

echo "== 6. 设计约束 =="
CODE=$(curl -sS -m 15 -o /dev/null -w '%{http_code}' -X POST "$BASE/v1/chat/completions" \
  -H 'Content-Type: application/json' -d '{"messages":[{"role":"user","content":"hi"}]}' 2>/dev/null)
[ "$CODE" = "400" ] && ok "纯文本请求被拒（400 relay_no_audio）" || bad "纯文本请求返回 $CODE，应为 400"

echo
if [ "$FAIL" -eq 0 ]; then
  echo "结果：全部通过"
else
  echo "结果：$FAIL 项失败"
fi
exit "$FAIL"
