#!/bin/sh
# 轻量看门狗：一声 curl 就返回（约 5ms，零 token）。
# 适合塞进任何「顺手的机会」里，例如 shell 提示符钩子、定时任务、你随手跑的脚本。
HERE="$(cd "$(dirname "$0")" && pwd)"
PORT="${RELAY_PORT:-8765}"
[ "$(curl -sf -m 1 "http://127.0.0.1:$PORT/health" 2>/dev/null)" ] && exit 0
exec "$HERE/preflight.sh" >/dev/null 2>&1
