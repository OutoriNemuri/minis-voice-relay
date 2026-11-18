#!/bin/sh
# Minis 语音中转开关：sh relay.sh start|stop|status|log
HERE="$(cd "$(dirname "$0")" && pwd)"
PID=${RELAY_TMP:-/tmp}/mvrelay.pid
case "$1" in
  start)
    if curl -sf -m 1 "http://127.0.0.1:${2:-8765}/health" >/dev/null 2>&1; then
      echo "已在运行（端口 ${2:-8765} 有响应）"
      [ -f "$PID" ] || pgrep -f "relay.py --port ${2:-8765}" > "$PID" 2>/dev/null
      exit 0
    fi
    [ -n "$DEEPSEEK_API_KEY" ] || { echo "缺少 DEEPSEEK_API_KEY"; exit 1; }
    (setsid python3 "$HERE/relay.py" --port "${2:-8765}" >> ${RELAY_LOG:-/tmp/mvrelay.log} 2>&1 &) >/dev/null 2>&1
    echo $! > "$PID"; sleep 2
    curl -s http://127.0.0.1:${2:-8765}/health && echo " ← 已启动 pid=$(cat $PID)" ;;
  stop)
    [ -f "$PID" ] && kill "$(cat $PID)" 2>/dev/null
    pkill -f "relay.py --port" 2>/dev/null
    rm -f "$PID"; echo stopped ;;
  status)
    if curl -sf -m 1 "http://127.0.0.1:8765/health" >/dev/null 2>&1; then
      P=$(cat "$PID" 2>/dev/null || pgrep -f "relay.py --port 8765" | head -1)
      echo "running pid=${P:-?}"
    else echo stopped; fi ;;
  log)   tail -n "${2:-20}" ${RELAY_LOG:-/tmp/mvrelay.log} ;;
  *) echo "用法: sh relay.sh start|stop|status|log" ;;
esac
