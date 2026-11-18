#!/bin/sh
# Minis 语音中转 —— 安装 / 运维
#
# 为什么有 autostart：中转是普通后台进程，App 被系统挂起后会一起停。
# iOS 上没有真正的开机 daemon，所以只能把「拉起」成本压到最低：
#   - 推荐：把 preflight.sh 接到你/AI 会话的第一次 shell 调用上（0.02s）；
#   - 可选：调 relay.sh start 显式启动。
# ⚠ Shortcuts「打开 App」自动化不推荐：每次切前后台都会触发一轮 Agent 对话，烧 token。
#
# 用法：
#   sh bin/autostart.sh save-key sk-xxx   # 保存密钥到 ~/.config/minis-voice-relay/key (600)
#   sh bin/autostart.sh run               # 起一次（幂等）
#   sh bin/autostart.sh install           # 可选：装 /etc/profile.d 钩子（login shell 时拉起）
#   sh bin/autostart.sh uninstall
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
HOOK=/etc/profile.d/99-minis-voice-relay.sh
KEYDIR="$HOME/.config/minis-voice-relay"
KEYFILE="$KEYDIR/key"
PORT="${RELAY_PORT:-8765}"
RELAY_LOG="${RELAY_LOG:-/tmp/mvrelay.log}"

healthy() { curl -sf -m 1 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; }

load_key() {
  [ -n "$DEEPSEEK_API_KEY" ] && return 0
  if [ -r "$KEYFILE" ]; then
    DEEPSEEK_API_KEY=$(cat "$KEYFILE")
  fi
  export DEEPSEEK_API_KEY
  [ -n "$DEEPSEEK_API_KEY" ]
}

case "$1" in
  save-key)
    [ -n "$2" ] || { echo "用法: sh autostart.sh save-key <sk-...>" >&2; exit 2; }
    mkdir -p "$KEYDIR"; umask 077
    printf '%s' "$2" > "$KEYFILE"; chmod 600 "$KEYFILE"
    echo "已保存到 $KEYFILE（600，仅本机可读）";;

  run)
    healthy && { echo "already running"; exit 0; }
    load_key || { echo "缺少 DEEPSEEK_API_KEY" >&2; exit 1; }
    sh "$HERE/preflight.sh";;

  install)
    load_key >/dev/null 2>&1 || echo "提示：尚无可用的 key，装完钩子后仍需 save-key" >&2
    cat > "$HOOK" <<INNER
# minis-voice-relay: 任何 login shell 顺手把中转拉起来（零 token）
[ -x "$ROOT/bin/preflight.sh" ] && "$ROOT/bin/preflight.sh" >/dev/null 2>&1
INNER
    chmod +x "$HOOK"
    echo "已安装 $HOOK";;

  uninstall) rm -f "$HOOK"; echo "已移除 $HOOK";;

  voice)
    mkdir -p "$KEYDIR"
    case "$2" in
      stella|tide) ;;
      "") python3 -c "import json;print(json.load(open('$KEYDIR/config.json')).get('voice','stella'))" 2>/dev/null || echo "stella"; exit 0 ;;
      *) echo "可选音色只有 stella（女声）/ tide（男声）" >&2; exit 2 ;;
    esac
    python3 - "$KEYDIR/config.json" "$2" <<'PY'
import json, os, sys
path, voice = sys.argv[1], sys.argv[2]
cfg = {}
if os.path.exists(path):
    try: cfg = json.load(open(path))
    except Exception: cfg = {}
cfg["voice"] = voice
json.dump(cfg, open(path, "w"), ensure_ascii=False, indent=2)
PY
    echo "默认音色已设为 $2（重启生效：sh bin/relay.sh stop && sh bin/preflight.sh）";;

  doctor)
    echo "root      : $ROOT"
    echo "port      : $PORT"
    healthy && echo "health    : ok" || echo "health    : down"
    [ -n "$DEEPSEEK_API_KEY" ] && echo "key       : 环境变量"
    [ -r "$KEYFILE" ] && echo "key       : $KEYFILE"
    [ -r "$KEYDIR/config.json" ] && echo "config    : $(cat "$KEYDIR/config.json")" || echo "config    : 无（音色默认 stella）"
    [ -x "$HOOK" ] && echo "profile.d : 已安装" || echo "profile.d : 未安装"
    command -v ffmpeg >/dev/null && echo "ffmpeg    : $(command -v ffmpeg)" || echo "ffmpeg    : 缺失（mp3/opus 输出会降级或失败）"
    pgrep -f "relay.py --port" >/dev/null && echo "process   : $(pgrep -f 'relay.py --port' | tr '\n' ' ')" || echo "process   : 无";;

  *) echo "用法: sh autostart.sh save-key <sk-...> | voice [stella|tide] | run | install | uninstall | doctor";;
esac
