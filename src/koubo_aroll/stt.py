"""语音识别。默认本地 faster-whisper；OPENAI_API_KEY 存在时可走兼容接口。"""

from __future__ import annotations

import json
import os
import uuid
import urllib.error
import urllib.request
from pathlib import Path

from koubo_aroll.errors import KouboError


class SttError(KouboError):
    pass


def transcript_from_segments(
    segments: list[dict],
    *,
    source: str,
    language: str,
    duration: float | None = None,
    engine: str = "",
) -> dict:
    normalized: list[dict] = []
    for index, seg in enumerate(segments):
        words = []
        for word in seg.get("words") or []:
            text = word.get("text") if word.get("text") is not None else word.get("word", "")
            words.append(
                {
                    "start": round(float(word["start"]), 3),
                    "end": round(float(word["end"]), 3),
                    "text": text or "",
                }
            )
        normalized.append(
            {
                "id": index,
                "start": round(float(seg.get("start", 0)), 3),
                "end": round(float(seg.get("end", 0)), 3),
                "text": seg.get("text") or "",
                "words": words,
            }
        )
    payload: dict = {
        "source": source,
        "language": language or "zh",
        "duration": None if duration is None else round(float(duration), 3),
        "segments": normalized,
    }
    if engine:
        payload["engine"] = engine
    return payload


def _attach_words(segments: list[dict], words: list[dict]) -> None:
    for word in words:
        placed = False
        start = float(word["start"])
        for seg in segments:
            if start >= float(seg["start"]) and start < float(seg["end"]):
                seg.setdefault("words", []).append(word)
                placed = True
                break
        if not placed and segments:
            segments[-1].setdefault("words", []).append(word)


def transcript_from_openai(payload: dict, source: str, *, engine: str) -> dict:
    raw_words = []
    for word in payload.get("words") or []:
        raw_words.append(
            {
                "start": float(word["start"]),
                "end": float(word["end"]),
                "text": word.get("text") or word.get("word") or "",
            }
        )
    segments = []
    for index, seg in enumerate(payload.get("segments") or []):
        words = []
        for word in seg.get("words") or []:
            words.append(
                {
                    "start": float(word["start"]),
                    "end": float(word["end"]),
                    "text": word.get("text") or word.get("word") or "",
                }
            )
        segments.append(
            {
                "id": seg.get("id", index),
                "start": float(seg.get("start", 0)),
                "end": float(seg.get("end", 0)),
                "text": seg.get("text") or "",
                "words": words,
            }
        )
    if raw_words and not segments:
        end = max(word["end"] for word in raw_words)
        segments = [
            {
                "id": 0,
                "start": 0.0,
                "end": end,
                "text": payload.get("text") or "",
                "words": [],
            }
        ]
    if raw_words and segments and all(not seg["words"] for seg in segments):
        _attach_words(segments, raw_words)
    if not segments:
        raise SttError("识别结果没有时间轴，无法按口令切视频。")
    return transcript_from_segments(
        segments,
        source=source,
        language=str(payload.get("language") or "zh"),
        duration=payload.get("duration"),
        engine=engine,
    )


def load_transcript(path: Path, source_name: str) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SttError(f"字幕 JSON 无法解析：{path.name}（{exc}）") from exc
    if isinstance(data, list):
        data = {"segments": data}
    if not isinstance(data, dict) or "segments" not in data:
        raise SttError(f"{path.name} 需要包含 segments 数组，每段要有 start、end、text。")
    return transcript_from_segments(
        data.get("segments") or [],
        source=source_name,
        language=str(data.get("language") or "zh"),
        duration=data.get("duration"),
        engine=str(data.get("engine") or "json"),
    )


def build_initial_prompt(open_marker: str, close_marker: str) -> str:
    return f"中文口播。说话人会清楚地说出口令「{open_marker}」和「{close_marker}」。"


def transcribe_whisper(
    wav_path: Path,
    *,
    source: str,
    model_name: str,
    device: str,
    compute_type: str,
    language: str,
    initial_prompt: str,
    vad: bool,
) -> dict:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SttError(
            "未安装本地识别依赖 faster-whisper。可以执行 pip install -e \".[stt]\"，"
            "或先准备同名 JSON，再用 --stt json。"
        ) from exc
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, info = model.transcribe(
        str(wav_path),
        language=language or None,
        word_timestamps=True,
        vad_filter=vad,
        initial_prompt=initial_prompt,
        condition_on_previous_text=False,
        beam_size=5,
    )
    rows = []
    for seg in segments:
        words = []
        for word in seg.words or []:
            words.append({"start": word.start, "end": word.end, "text": word.word})
        rows.append({"start": seg.start, "end": seg.end, "text": seg.text, "words": words})
    return transcript_from_segments(
        rows,
        source=source,
        language=getattr(info, "language", None) or language or "zh",
        duration=getattr(info, "duration", None),
        engine=f"faster-whisper:{model_name}",
    )


def _multipart(fields: list[tuple[str, str]], file_field: str, filename: str, content: bytes) -> tuple[str, bytes]:
    boundary = "----koubo" + uuid.uuid4().hex
    body = bytearray()
    for key, value in fields:
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        body.extend(value.encode())
        body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        (
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            "Content-Type: audio/wav\r\n\r\n"
        ).encode()
    )
    body.extend(content)
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    return boundary, bytes(body)


def transcribe_openai(
    wav_path: Path,
    *,
    source: str,
    language: str,
    model: str | None = None,
) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SttError("使用 --stt openai 时需要设置环境变量 OPENAI_API_KEY。")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model_name = model or os.environ.get("OPENAI_TRANSCRIBE_MODEL", "whisper-1")
    content = wav_path.read_bytes()
    fields = [
        ("model", model_name),
        ("response_format", "verbose_json"),
        ("timestamp_granularities[]", "segment"),
        ("timestamp_granularities[]", "word"),
    ]
    if language:
        fields.append(("language", language))
    boundary, body = _multipart(fields, "file", wav_path.name, content)
    request = urllib.request.Request(
        f"{base}/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise SttError(f"付费识别接口返回 {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise SttError(f"无法连接识别接口：{exc.reason}") from exc
    return transcript_from_openai(payload, source, engine=f"openai:{model_name}")


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True
