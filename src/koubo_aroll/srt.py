"""SRT 读写。剪映专业版导入 UTF-8、无 BOM 的 SRT。"""

from __future__ import annotations

from koubo_aroll.markers import Cue, display_text


def format_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_clock(seconds: float) -> str:
    return format_timestamp(seconds).replace(",", ".")


def render_srt(cues: list[Cue], *, timebase: str = "out") -> str:
    """timebase 为 out 时用成片时间，为 src 时用原片时间。"""
    if timebase not in {"out", "src"}:
        raise ValueError("timebase 只能是 out 或 src。")
    blocks: list[str] = []
    previous_end = 0.0
    for cue in cues:
        text = display_text(cue.text)
        if not text:
            continue
        if timebase == "out":
            start, end = cue.out_start, cue.out_end
        else:
            start, end = cue.src_start, cue.src_end
        start = max(start, previous_end)
        end = max(end, start + 0.08)
        blocks.append(
            f"{len(blocks) + 1}\n{format_timestamp(start)} --> {format_timestamp(end)}\n{text}\n"
        )
        previous_end = end
    if not blocks:
        return ""
    return "\n".join(blocks) + "\n"


def segments_to_srt(segments: list[dict]) -> str:
    cues: list[Cue] = []
    for seg in segments:
        text = display_text(seg.get("text") or "")
        if not text:
            continue
        cues.append(
            Cue(
                src_start=float(seg.get("start", 0)),
                src_end=float(seg.get("end", 0)),
                text=text,
                out_start=float(seg.get("start", 0)),
                out_end=float(seg.get("end", 0)),
            )
        )
    return render_srt(cues, timebase="src")
