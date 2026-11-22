#!/usr/bin/env python3
"""Minis Voice Relay —— 让 Minis 的语音输入/输出接上 DeepSeek ASR / TTS。

背景：Minis 的语音输入/输出只能选「模型」，且要求该模型带 `audio_input` /
`audio_output` 模态；而 DeepSeek 没有音频模型。这个中转把两个语音端点接到
DeepSeek 的 ASR / TTS 服务上：

  POST /v1/audio/transcriptions   multipart file  →  api.deepseek.com/v1/tools/asr
  POST /v1/audio/speech           JSON input      →  api.deepseek.com/v1/tools/tts
  POST /v1/chat/completions       无音频 → 400（本端点只做语音，不提供对话）
  GET  /health                    {"ok": true}

跑法：
  export DEEPSEEK_API_KEY=sk-...
  python3 bin/relay.py --port 8765
  # Minis：设置 → 模型服务商 → 添加 OpenAI 兼容 provider，Base URL = http://127.0.0.1:8765
  # 再建两个模型，模态分别勾 audio_input / audio_output

自测（不启服务）：
  python3 bin/relay.py --selftest audio.wav
"""
from __future__ import annotations

import argparse
import base64
import http.server
import binascii
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEEPSEEK = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
VOICE_MODEL = "deepseek-flash"
LOG_PATH = os.path.join(tempfile.gettempdir(), "mvrelay.log")


# ---------------------------------------------------------------- 音频处理
WAV_RATES = (8000, 16000, 22050, 24000, 32000, 44100, 48000)


def wav_ok(blob: bytes) -> str:
    """返回问题描述，空字符串表示可直接送 ASR。"""
    if len(blob) < 44 or blob[:4] != b"RIFF" or blob[8:12] != b"WAVE":
        return "不是 WAV"
    pos = 12
    while pos + 8 <= len(blob):
        cid = blob[pos:pos + 4]
        size = struct.unpack("<I", blob[pos + 4:pos + 8])[0]
        if cid == b"fmt ":
            fmt, ch, rate = struct.unpack("<HHI", blob[pos + 8:pos + 16])
            bits = struct.unpack("<H", blob[pos + 22:pos + 24])[0]
            if fmt not in (1, 0xFFFE):
                return f"非 PCM（fmt={fmt}）"
            if bits != 16:
                return f"{bits} 位"
            if rate not in WAV_RATES:
                return f"{rate} Hz"
            if ch not in (1, 2):
                return f"{ch} 声道"
            return ""
        pos += 8 + size + (size & 1)
    return "无 fmt 块"


def pcm_to_wav(blob: bytes, rate=24000, channels=1) -> bytes:
    size = len(blob)
    return (b"RIFF" + struct.pack("<I", 36 + size) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * channels * 2,
                          channels * 2, 16)
            + b"data" + struct.pack("<I", size) + blob)


def normalize_audio(raw: bytes, fmt: str | None) -> bytes:
    """把随手收到的音频变成 ASR 能吃的 16 位 WAV（必要时用 ffmpeg 转码/截断）。"""
    blob = raw
    if fmt and fmt.lower() in ("pcm", "s16le", "raw") and not blob[:4] == b"RIFF":
        # 裸 PCM：Minis / DeepSeek 默认都是 24 kHz 单声道
        blob = pcm_to_wav(blob)
    problem = wav_ok(blob)
    if not problem:
        return blob
    if not _has_ffmpeg():
        raise RuntimeError(f"音频不合规（{problem}）且没有 ffmpeg 可转码")
    src = tempfile.mktemp(suffix=".bin")
    dst = tempfile.mktemp(suffix=".wav")
    with open(src, "wb") as f:
        f.write(blob)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
           "-t", "120", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst]
    r = subprocess.run(cmd, capture_output=True, text=True)
    os.remove(src)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg 转码失败：" + (r.stderr.strip()[:300] or "unknown"))
    with open(dst, "rb") as f:
        out = f.read()
    os.remove(dst)
    return out


def _has_ffmpeg():
    import shutil
    return bool(shutil.which("ffmpeg"))


# ---------------------------------------------------------------- DeepSeek
def deepseek_asr(wav: bytes) -> dict:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY")
    boundary = "----relay" + str(int(time.time() * 1000))
    body = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
            f"Content-Type: audio/wav\r\n\r\n").encode() + wav + \
           f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{DEEPSEEK}/v1/tools/asr", data=body, method="POST")
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def deepseek_chat(body: dict, key_header: str | None) -> tuple[int, str, str]:
    """把非音频请求转发给 DeepSeek 文本模型，流式原样回吐。"""
    model = VOICE_MODEL
    fwd = dict(body)
    fwd["model"] = model
    # 去掉音频块，避免上游 400
    for m in fwd.get("messages") or []:
        if isinstance(m.get("content"), list):
            m["content"] = [b for b in m["content"]
                            if b.get("type") not in ("input_audio", "audio")]
    data = json.dumps(fwd, ensure_ascii=False).encode()
    req = urllib.request.Request(f"{DEEPSEEK}/chat/completions", data=data, method="POST")
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip() or (key_header or "")
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", "application/json")
    resp = urllib.request.urlopen(req, timeout=600)
    return resp.status, resp.headers.get("Content-Type", "application/json"), resp.read()


# ---------------------------------------------------------------- TTS
VOICE_ALIASES = {
    "alloy": "stella", "echo": "tide", "fable": "stella", "onyx": "tide",
    "nova": "stella", "shimmer": "stella", "sage": "tide", "coral": "stella",
}
VALID_VOICES = ("stella", "tide")
CFG_DIR = os.path.expanduser("~/.config/minis-voice-relay")
CFG_PATH = os.path.join(CFG_DIR, "config.json")
DEFAULT_VOICE = "stella"

# /v1/models 里对外声明的模型。模态字段多写几种常见拼法，客户端认哪个都能取到，
# 这样 Minis 拉一次列表就能同时发现 ASR 与 TTS，不用手工添加。
MODELS = (
    ("deepseek-asr-relay", "语音转写（DeepSeek ASR）", "audio_input"),
    ("deepseek-tts-relay", "语音合成（DeepSeek TTS）", "audio_output"),
)


def model_list() -> list:
    now = int(time.time())
    out = []
    for model_id, label, modality in MODELS:
        out.append({
            "id": model_id,
            "object": "model",
            "created": now,
            "owned_by": "relay",
            "name": label,
            "modality": modality,
            "modalities": [modality],
            "capabilities": [modality],
            "output_modalities": [modality] if modality == "audio_output" else ["text"],
            "input_modalities": ["audio", "text"] if modality == "audio_input" else ["text"],
        })
    return out


def load_config() -> dict:
    """读 ~/.config/minis-voice-relay/config.json；坏了就当空的，不阻断服务。"""
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def normalize_voice(value: str | None) -> str | None:
    """把各种写法（tide / TIDE / echo / onyx）收敛成 stella 或 tide。"""
    if not value:
        return None
    v = str(value).strip().lower()
    v = VOICE_ALIASES.get(v, v)
    return v if v in VALID_VOICES else None


def pick_voice(payload: dict) -> str:
    """决定这次合成用哪个音色。

    1) 模型名后缀：deepseek-tts-tide / *-stella（Minis 只能选模型，这是最顺手的开关）
    2) 请求体里的 voice / voice_id
    3) config.json 的 voice；再不行用 stella
    """
    model = str(payload.get("model") or "").lower()
    for v in VALID_VOICES:
        if model.endswith("-" + v) or model.endswith("_" + v):
            return v
    for key in ("voice", "voice_id"):
        got = normalize_voice(payload.get(key))
        if got:
            return got
    return normalize_voice(load_config().get("voice")) or DEFAULT_VOICE

FMT_MAP = {
    "mp3": "mp3", "opus": "ogg", "aac": "wav", "flac": "wav",
    "wav": "wav", "pcm": "pcm", "s16le": "pcm",
}


def deepseek_tts(text: str, voice: str = "stella", out_format: str = "wav"):
    """调 /v1/tools/tts，返回 (音频字节, 实际格式)。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY")
    fmt = FMT_MAP.get((out_format or "wav").lower(), "wav")
    up = fmt if fmt in ("wav", "pcm") else "wav"      # 上游只给 wav / pcm
    # 上游只认 `voice_id`（voice_id=tide 实测基频 ~100 Hz，而 voice=tide 仍是女声 ~235 Hz）
    body = json.dumps({"text": text, "voice_id": voice, "format": up,
                       "filter_markdown": True}, ensure_ascii=False).encode()
    req = urllib.request.Request(f"{DEEPSEEK}/v1/tools/tts", data=body, method="POST")
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=600) as r:
        audio = r.read()
    if fmt == "pcm":
        return pcm_to_wav(audio, 24000, 1), "wav"
    if fmt == "wav":
        return audio, "wav"
    # mp3 / ogg：本地 ffmpeg 转码
    if not _has_ffmpeg():
        raise RuntimeError(f"需要 ffmpeg 转成 {fmt}")
    src = tempfile.mktemp(suffix=".wav")
    dst = tempfile.mktemp(suffix="." + fmt)
    with open(src, "wb") as f:
        f.write(audio)
    if fmt == "mp3":
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
               "-c:a", "libmp3lame", "-b:a", "64k", dst]
    else:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src, dst]
    r = subprocess.run(cmd, capture_output=True, text=True)
    os.remove(src)
    if r.returncode != 0:
        raise RuntimeError("转码失败：" + (r.stderr.strip()[:200] or "unknown"))
    with open(dst, "rb") as f:
        out = f.read()
    os.remove(dst)
    return out, fmt


MIME = {"wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg", "pcm": "audio/pcm"}


# ---------------------------------------------------------------- 内容提取
def find_audio(payload: dict):
    """在 messages 里找第一个 input_audio，返回 (bytes, format)。"""
    for m in payload.get("messages") or []:
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "input_audio":
                inner = block.get("input_audio") or {}
                return _decode(inner), inner.get("format")
            if block.get("type") == "audio" and isinstance(block.get("audio"), dict):
                return _decode(block["audio"]), None
    return None, None


def _decode(inner: dict) -> bytes:
    data = inner.get("data") or ""
    if isinstance(data, str) and data.startswith("data:"):
        data = data.split(",", 1)[-1]
    try:
        return base64.b64decode(data, validate=False)
    except (binascii.Error, ValueError) as e:
        raise RuntimeError(f"input_audio 不是合法 base64：{e}")


def completion(text: str, model: str = "deepseek-asr-relay") -> dict:
    return {
        "id": "chatcmpl-relay-" + str(int(time.time() * 1000)),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def sse(text: str, model: str = "deepseek-asr-relay") -> bytes:
    head = {"id": "chatcmpl-relay", "object": "chat.completion.chunk",
            "created": int(time.time()), "model": model}
    out = []
    first = dict(head, choices=[{"index": 0, "delta": {"role": "assistant", "content": ""}}])
    out.append("data: " + json.dumps(first, ensure_ascii=False))
    # 按 12 字切片，前端更像"边说边出"
    for i in range(0, len(text), 12):
        chunk = dict(head, choices=[{"index": 0, "delta": {"content": text[i:i + 12]}}])
        out.append("data: " + json.dumps(chunk, ensure_ascii=False))
    done = dict(head, choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}])
    out.append("data: " + json.dumps(done, ensure_ascii=False))
    out.append("data: [DONE]")
    return ("\n\n".join(out) + "\n\n").encode()


# ---------------------------------------------------------------- HTTP
def _write_log(line: str):
    """唯一的落盘入口。

    以前 print 到 stderr（已被启动脚本重定向进同一个文件）再额外写一遍文件，
    结果每行都出现两次。现在只写文件；stderr 只留真正的异常回溯。
    """
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        print(line, file=sys.stderr, flush=True)


def log(msg: str):
    _write_log(f"[{time.strftime('%H:%M:%S')}] {msg}")


def dump(payload, tag="request"):
    """把完整请求体落盘，格式对不上时第一手证据。"""
    path = os.path.join(tempfile.gettempdir(), f"mvrelay_{tag}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log(f"{tag} 已存到 {path}（{len(json.dumps(payload))} 字符）")
    except OSError:
        pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # 把每个请求直接打到控制台（不写日志文件），方便在 App 里一眼看到
    verbose = True

    def log_message(self, fmt, *a):
        if self.verbose:
            log(f"<- {self.command} {self.path}  {fmt % a}")

    def _multipart_audio(self, raw: bytes):
        """解析 multipart，返回第一段带 filename 的音频字节。

        手写解析而不是 cgi.FieldStorage：Alpine 的 Python 已移除 cgi，
        这里只需要 boundary 切分 + 头尾 \\r\\n 处理。
        """
        ctype = self.headers.get("Content-Type") or ""
        m = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', ctype)
        if not m:
            return None
        boundary = (m.group(1) or m.group(2)).strip().encode()
        for part in raw.split(b"--" + boundary):
            part = part.strip(b"\r\n")
            if not part or b"\r\n\r\n" not in part:
                continue
            head, body = part.split(b"\r\n\r\n", 1)
            first = head.split(b"\r\n")[0]
            if b"filename=" in first or b"name=\"file\"" in first or b"name=\"audio\"" in first:
                if body.endswith(b"\r\n"):
                    body = body[:-2]
                return body
        return None

    def _stt_multipart(self, raw: bytes):
        """POST /v1/audio/transcriptions → {"text": ...}"""
        audio = self._multipart_audio(raw)
        if audio is None:
            fmt = "multipart 里没有 file 字段"
            log(f"!!! {fmt}")
            return self._json(400, {"error": {"message": f"relay: {fmt}"}})
        log(f"转录请求 {len(audio)} 字节音频 → ASR")
        try:
            wav = normalize_audio(audio, None)
            result = deepseek_asr(wav)
            text = (result.get("text") or "").strip()
            log(f"转写完成 {result.get('duration_ms')}ms：{text[:60]}")
        except Exception as e:
            log(f"ASR 失败：{e}")
            return self._json(502, {"error": {"message": f"relay: {e}"}})
        return self._json(200, {"text": text})

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _catch_all(self):
        """未知路径也答一份，方便反查 Minis 实际请求的 URL。"""
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        log(f"!!! 未知路径 {self.command} {self.path} ({len(raw)} bytes)")
        with open("/tmp/mvrelay_route.txt", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {self.command} {self.path} {len(raw)}\n")
            for k, v in self.headers.items():
                if k.lower() not in ("authorization",):
                    f.write(f"    {k}: {v}\n")
        if raw[:1] in (b"{", b"["):
            with open("/tmp/mvrelay_raw.json", "wb") as f:
                f.write(raw)
        audio, _ = (None, None)
        try:
            audio, _ = find_audio(json.loads(raw.decode("utf-8", "replace") or "{}"))
        except Exception:
            pass
        if audio is not None:
            try:
                text = (deepseek_asr(normalize_audio(audio, None)).get("text") or "").strip()
            except Exception as e:
                return self._json(502, {"error": {"message": f"relay: {e}"}})
            if self.path.rstrip("/").endswith("transcriptions") or b"multipart" in (self.headers.get("Content-Type") or "").encode():
                return self._json(200, {"text": text})
            return self._json(200, completion(text))
        return self._json(404, {"error": {"message": f"relay: 未知路径 {self.path}"}})

    def _json(self, code: int, obj: dict):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/v1/models") or self.path.startswith("/models"):
            return self._json(200, {"object": "list", "data": model_list()})
        if self.path.startswith("/health"):
            return self._json(200, {"ok": True})
        return self._catch_all()

    def _tts(self, payload: dict):
        """POST /v1/audio/speech → DeepSeek /v1/tools/tts。

        音色优先级：模型名里的后缀 > 请求里的 voice/voice_id > 配置默认值。
        Minis 的语音输出只能选模型、没有音色选项，所以「选音色」只能在这里做：
        要么建两个模型名（…-stella / …-tide），要么改 ~/.config/minis-voice-relay/config.json。
        """
        text = (payload.get("input") or payload.get("text") or "").strip()
        if not text:
            return self._json(400, {"error": {"message": "input 为空"}})
        voice = pick_voice(payload)
        fmt = payload.get("response_format") or payload.get("format") or "wav"
        log(f"合成请求 {len(text)} 字 voice={voice} format={fmt}")
        try:
            audio, actual = deepseek_tts(text, voice, fmt)
        except Exception as e:
            log(f"TTS 失败：{e}")
            return self._json(502, {"error": {"message": f"relay: {e}"}})
        log(f"合成完成 {len(audio)} 字节（{actual}）")
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(actual, "application/octet-stream"))
        self.send_header("Content-Length", str(len(audio)))
        self.end_headers()
        self.wfile.write(audio)

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")
        if path.endswith("/audio/speech"):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8", "replace") or "{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": {"message": "body 不是 JSON"}})
            dump(payload, "tts")
            return self._tts(payload)
        if path.endswith("/audio/transcriptions"):
            length = int(self.headers.get("Content-Length") or 0)
            return self._stt_multipart(self.rfile.read(length) if length else b"")
        if not (path.startswith("/v1/chat/completions") or path.startswith("/chat/completions")):
            return self._catch_all()
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8", "replace") or "{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": {"message": "body 不是 JSON"}})
        log(f"POST {self.path}  ({len(raw)} bytes, model={payload.get('model')!r})")
        dump(payload)
        audio, fmt = find_audio(payload)
        if audio is None:
            # 这个端点只服务语音：不带音频的请求一律拒绝，避免它变成"另一个聊天模型"
            log("无音频块 → 拒绝（该模型只做语音转写，不提供对话）")
            return self._json(400, {"error": {
                "message": "这是语音转写模型：请求里没有音频。想聊天请切换到普通文本模型。",
                "type": "relay_no_audio"}})
            try:
                code, ctype, body = deepseek_chat(payload, self.headers.get("Authorization"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:500]
                log(f"上游 {e.code}: {detail}")
                return self._json(e.code, {"error": {"message": detail}})
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        log(f"收到音频 {len(audio)} 字节（format={fmt!r}）→ ASR")
        try:
            wav = normalize_audio(audio, fmt)
            result = deepseek_asr(wav)
            text = (result.get("text") or "").strip()
            log(f"转写完成 {result.get('duration_ms')}ms：{text[:60]}")
        except Exception as e:              # 任何失败都要让前端看到原因
            log(f"ASR 失败：{e}")
            return self._json(502, {"error": {"message": f"relay: {e}", "type": "relay_error"}})
        if payload.get("stream"):
            data = sse(text)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._json(200, completion(text))


def serve(port: int, host: str = "127.0.0.1"):
    try:
        srv = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        # 端口被占：多半是上次的进程还活着，或者另一个实例在跑。别静默失败。
        log(f"启动失败：端口 {port} 无法绑定（{e}）。"
            f"已被占用的话直接访问 /health 确认，或停掉旧进程。")
        sys.exit(2)
    log(f"voice relay 监听 http://{host}:{port}（文本模型 {VOICE_MODEL}，默认音色 {pick_voice({})}，日志 {LOG_PATH}）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("已停止")


def selftest(path: str):
    with open(path, "rb") as f:
        blob = f.read()
    wav = normalize_audio(blob, None)
    r = deepseek_asr(wav)
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Minis Voice Relay —— STT/TTS 双向中转 → DeepSeek ASR / TTS")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="监听地址，默认仅本机可见")
    ap.add_argument("--voice-model", default="deepseek-flash",
                    help="非音频请求转发到哪个文本模型")
    ap.add_argument("--voice", choices=VALID_VOICES,
                    help="默认音色（也可写进 config.json 的 voice）")
    ap.add_argument("--selftest", help="直接测一段音频（不启服务）")
    a = ap.parse_args()
    VOICE_MODEL = a.voice_model
    if a.voice:
        DEFAULT_VOICE = a.voice      # 命令行优先级最高，覆盖 config.json
    if a.selftest:
        selftest(a.selftest)
    else:
        serve(a.port, a.host)
