#!/bin/sh
# Minis 语音中转 —— 启动前检查（幂等，零 token）
#
# iOS 上 Minis 打开 App 时不会执行任何 shell 脚本，shell 只在你/AI 真正跑命令时
# 才存在。所以「用语音之前」唯一可挂的钩子，就是本会话里的第一次 shell 调用。
#
#   sh bin/preflight.sh          # 已跑 → already running（0.02s）
#                                # 没跑 → started（0.7s）
# 端口可用 RELAY_PORT 覆盖，默认 8765。
HERE="$(cd "$(dirname "$0")" && pwd)"
PORT="${RELAY_PORT:-8765}"
RELAY_LOG="${RELAY_LOG:-/tmp/mvrelay.log}"
KEYFILE="${MVRELAY_KEYFILE:-$HOME/.config/minis-voice-relay/key}"

if curl -sf -m 1 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "relay: already running"
  exit 0
fi

if [ -z "$DEEPSEEK_API_KEY" ]; then
  if [ -r "$KEYFILE" ]; then
    DEEPSEEK_API_KEY=$(cat "$KEYFILE"); export DEEPSEEK_API_KEY

# 文件从网盘/压缩包拷过来常常丢执行位，这里顺手兜一下，避免「服务起不来还不知道为什么」
for f in "$HERE"/*.sh "$HERE"/relay.py; do
  [ -f "$f" ] && [ ! -x "$f" ] && chmod +x "$f" 2>/dev/null
done
    echo "relay: key 来自 $KEYFILE"
  fi
fi
export DEEPSEEK_API_KEY

# 文件从网盘/压缩包拷过来常常丢执行位，这里顺手兜一下，避免「服务起不来还不知道为什么」
for f in "$HERE"/*.sh "$HERE"/relay.py; do
  [ -f "$f" ] && [ ! -x "$f" ] && chmod +x "$f" 2>/dev/null
done

if [ -z "$DEEPSEEK_API_KEY" ]; then
  echo "relay: 缺少 DEEPSEEK_API_KEY —— 先跑 sh bin/autostart.sh save-key <sk-...>" >&2
  exit 1
fi

(setsid python3 "$HERE/relay.py" --port "$PORT" >>"$RELAY_LOG" 2>&1 &) >/dev/null 2>&1

i=0
while [ $i -lt 20 ]; do
  sleep 0.3
  if curl -sf -m 1 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    echo "relay: started"
    exit 0
  fi
  i=$((i + 1))
done
echo "relay: 启动超时，看 $RELAY_LOG" >&2
exit 1
