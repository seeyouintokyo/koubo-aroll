"""口令状态机：只保留「拍错了」和「可以保留」中间的内容。

规则：
- 「拍错了」开口。开口令本身、以及它之前的内容，都不进入粗剪。
- 「可以保留」收口。收口令本身不进入粗剪。
- 窗口里又听到一次开口令：上一段未收口的内容作废，从新的开口令重新开始。
- 说到结尾仍未收口：整段丢弃，并给出警告。
- 收口令出现在开口令之前：忽略该收口令。
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_OPEN = "拍错了"
DEFAULT_CLOSE = "可以保留"
FUZZY_OPEN = {DEFAULT_OPEN: ("拍错啦",)}

_SKIP = set(
    " \t\r\n\u3000"
    "，。！？、,.!?;；：:"
    "\"'“”‘’「」『』【】（）()[]《》〈〉<>"
    "…—～~·-–"
    "\ufeff\u200b\u200c\u200d"
)


@dataclass
class Cue:
    src_start: float
    src_end: float
    text: str
    out_start: float = 0.0
    out_end: float = 0.0


@dataclass
class Span:
    src_start: float
    src_end: float
    text: str
    cues: list[Cue] = field(default_factory=list)
    out_start: float = 0.0
    out_end: float = 0.0


@dataclass
class Drop:
    reason: str
    src_start: float
    src_end: float
    text: str


@dataclass
class MarkerResult:
    keeps: list[Span]
    dropped: list[Drop]
    warnings: list[str]
    open_marker: str
    close_marker: str
    open_aliases: tuple[str, ...]
    close_aliases: tuple[str, ...]
    timing: str


@dataclass
class _Tick:
    ch: str
    start: float
    end: float


def is_skippable(ch: str) -> bool:
    return ch in _SKIP or ch.isspace()


def display_text(text: str) -> str:
    """整理字幕：去掉中文之间的空格，保留标点。"""
    if not text:
        return ""
    out: list[str] = []
    for ch in text.replace("\u3000", " "):
        if ch in "\r\n\t":
            ch = " "
        if ch == " ":
            if not out or out[-1] == " ":
                continue
            prev = out[-1]
            if "\u4e00" <= prev <= "\u9fff":
                continue
            out.append(ch)
            continue
        if out and out[-1] == " " and "\u4e00" <= ch <= "\u9fff":
            out.pop()
        out.append(ch)
    return "".join(out).strip()


def meaningful(text: str) -> bool:
    return any(not is_skippable(ch) for ch in text)


def dedupe(items: list[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for item in items:
        item = (item or "").strip()
        if item and item not in seen:
            seen.append(item)
    return tuple(seen)


def resolve_aliases(
    open_marker: str,
    close_marker: str,
    *,
    open_extra: tuple[str, ...] = (),
    close_extra: tuple[str, ...] = (),
    exact: bool = False,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not open_marker.strip() or not close_marker.strip():
        raise ValueError("开口令和收口令都不能为空。")
    opens = [open_marker.strip(), *open_extra]
    closes = [close_marker.strip(), *close_extra]
    if not exact:
        opens.extend(FUZZY_OPEN.get(open_marker.strip(), ()))
    open_aliases = dedupe(opens)
    close_aliases = dedupe(closes)
    if not open_aliases or not close_aliases:
        raise ValueError("开口令和收口令都不能为空。")
    return open_aliases, close_aliases


def timing_quality(transcript: dict) -> str:
    segments = transcript.get("segments") or []
    if not segments:
        return "empty"
    with_words = sum(1 for seg in segments if seg.get("words"))
    if with_words == len(segments):
        return "word"
    if with_words == 0:
        return "interpolated"
    return "mixed"


def _explode(text: str, start: float, end: float) -> list[_Tick]:
    chars = list(text or "")
    if not chars:
        return []
    start = float(start)
    end = float(end)
    if end < start:
        end = start
    step = (end - start) / len(chars) if chars else 0.0
    ticks: list[_Tick] = []
    for index, ch in enumerate(chars):
        tick_start = start + index * step
        tick_end = start + (index + 1) * step if step else start
        ticks.append(_Tick(ch, tick_start, tick_end))
    return ticks


def ticks_from_transcript(transcript: dict) -> list[_Tick]:
    ticks: list[_Tick] = []
    for seg in transcript.get("segments") or []:
        words = seg.get("words") or []
        if words:
            for word in words:
                ticks.extend(
                    _explode(
                        word.get("text") or word.get("word") or "",
                        word.get("start", seg.get("start", 0)),
                        word.get("end", seg.get("end", 0)),
                    )
                )
        else:
            ticks.extend(
                _explode(
                    seg.get("text") or "",
                    seg.get("start", 0),
                    seg.get("end", 0),
                )
            )
    return ticks


def _match_at(ticks: list[_Tick], index: int, alias: str) -> int | None:
    """若 ticks[index] 起能匹配 alias，返回匹配结束后的下标。标点和空白可以夹在字之间。"""
    if not alias or index >= len(ticks) or ticks[index].ch != alias[0]:
        return None
    cursor = index + 1
    for ch in alias[1:]:
        skipped = 0
        while cursor < len(ticks) and is_skippable(ticks[cursor].ch):
            skipped += 1
            if skipped > 4:
                return None
            cursor += 1
        if cursor >= len(ticks) or ticks[cursor].ch != ch:
            return None
        cursor += 1
    return cursor


def _match_any(ticks: list[_Tick], index: int, aliases: tuple[str, ...]) -> int | None:
    found: int | None = None
    for alias in sorted(aliases, key=len, reverse=True):
        end = _match_at(ticks, index, alias)
        if end is not None and (found is None or end > found):
            found = end
    return found


def _slice_text(ticks: list[_Tick], start: int, end: int) -> str:
    return display_text("".join(tick.ch for tick in ticks[start:end]))


def _in_keep(tick: _Tick, start: float, end: float) -> bool:
    mid = (tick.start + tick.end) / 2
    return (start - 1e-4) <= mid < (end - 1e-4)


def strip_markers(text: str, aliases: tuple[str, ...]) -> str:
    for alias in sorted(aliases, key=len, reverse=True):
        if alias:
            text = text.replace(alias, "")
    # 口令外面的引号，例如「拍错了」，不要留在正片开头。
    text = text.lstrip("".join(_SKIP))
    return display_text(text)


def _build_cues(
    ticks: list[_Tick],
    content_from: int,
    content_to: int,
    keep_start: float,
    keep_end: float,
    aliases: tuple[str, ...],
    cue_chars: int,
    cue_gap: float,
) -> list[Cue]:
    selected = [
        tick
        for tick in ticks[content_from:content_to]
        if _in_keep(tick, keep_start, keep_end)
    ]
    cues: list[Cue] = []
    buf: list[_Tick] = []

    def flush() -> None:
        if not buf:
            return
        text = strip_markers("".join(tick.ch for tick in buf), aliases)
        visible = [tick for tick in buf if not tick.ch.isspace()]
        buf.clear()
        if not meaningful(text) or not visible:
            return
        src_start = max(keep_start, visible[0].start)
        src_end = min(keep_end, visible[-1].end)
        if src_end <= src_start:
            src_end = min(keep_end, src_start + 0.12)
        if src_end <= src_start:
            return
        cues.append(Cue(src_start, src_end, text))

    for tick in selected:
        if buf and tick.start - buf[-1].end >= cue_gap:
            flush()
        buf.append(tick)
        visible = strip_markers("".join(item.ch for item in buf), aliases)
        if tick.ch in "。！？!?":
            flush()
        elif tick.ch in "，、,;；" and len(visible) >= cue_chars:
            flush()
        elif len(visible) >= cue_chars + 6:
            flush()
    flush()
    return cues


def _add_drop(
    dropped: list[Drop],
    ticks: list[_Tick],
    start: int,
    end: int,
    reason: str,
    src_start: float,
    src_end: float,
) -> None:
    if end <= start:
        return
    text = _slice_text(ticks, start, end)
    if not meaningful(text):
        return
    if src_end < src_start:
        src_end = src_start
    dropped.append(Drop(reason, src_start, src_end, text))


def find_keeps(
    transcript: dict,
    *,
    open_marker: str = DEFAULT_OPEN,
    close_marker: str = DEFAULT_CLOSE,
    open_aliases: tuple[str, ...] | None = None,
    close_aliases: tuple[str, ...] | None = None,
    exact: bool = False,
    head_pad: float = 0.0,
    tail_pad: float = 0.0,
    min_keep: float = 0.2,
    cue_chars: int = 16,
    cue_gap: float = 0.6,
) -> MarkerResult:
    if open_aliases is None or close_aliases is None:
        open_aliases, close_aliases = resolve_aliases(
            open_marker,
            close_marker,
            open_extra=open_aliases or (),
            close_extra=close_aliases or (),
            exact=exact,
        )
    else:
        open_aliases = dedupe([open_marker, *open_aliases])
        close_aliases = dedupe([close_marker, *close_aliases])

    all_aliases = open_aliases + close_aliases
    ticks = ticks_from_transcript(transcript)
    warnings: list[str] = []
    if open_marker and close_marker and (open_marker in close_marker or close_marker in open_marker):
        warnings.append("开口令和收口令互相包含，匹配可能不稳定。")

    keeps: list[Span] = []
    dropped: list[Drop] = []
    in_window = False
    window_from = 0
    open_end = 0.0
    outside_from = 0
    index = 0

    def time_at(pos: int, edge: str) -> float:
        if not ticks:
            return 0.0
        if pos <= 0:
            return ticks[0].start
        if pos >= len(ticks):
            return ticks[-1].end
        return ticks[pos].start if edge == "start" else ticks[pos - 1].end

    while index < len(ticks):
        if is_skippable(ticks[index].ch):
            index += 1
            continue
        opened = _match_any(ticks, index, open_aliases)
        closed = _match_any(ticks, index, close_aliases)

        if in_window and opened is not None:
            _add_drop(
                dropped,
                ticks,
                window_from,
                index,
                "restarted",
                open_end,
                ticks[index].start,
            )
            warnings.append(
                f"再次听到「{open_marker}」，已丢弃上一段未闭合内容并重新开始。"
            )
            open_end = ticks[opened - 1].end
            window_from = opened
            index = opened
            continue

        if in_window and closed is not None:
            close_start = ticks[index].start
            keep_start = open_end + head_pad
            keep_end = close_start - tail_pad
            text = strip_markers(_slice_text(ticks, window_from, index), all_aliases)
            duration = keep_end - keep_start
            if duration < min_keep or not meaningful(text):
                reason = "empty" if not meaningful(text) else "too_short"
                if meaningful(text):
                    dropped.append(Drop(reason, keep_start, max(keep_start, keep_end), text))
                warnings.append(
                    f"「{open_marker}」和「{close_marker}」之间没有足够内容，已跳过。"
                )
            else:
                cues = _build_cues(
                    ticks,
                    window_from,
                    index,
                    keep_start,
                    keep_end,
                    all_aliases,
                    cue_chars,
                    cue_gap,
                )
                if not cues:
                    cues = [Cue(keep_start, keep_end, text)]
                keeps.append(Span(keep_start, keep_end, text, cues))
            in_window = False
            outside_from = closed
            index = closed
            continue

        if not in_window and opened is not None:
            _add_drop(
                dropped,
                ticks,
                outside_from,
                index,
                "outside",
                time_at(outside_from, "start"),
                ticks[index].start,
            )
            in_window = True
            open_end = ticks[opened - 1].end
            window_from = opened
            index = opened
            continue

        if not in_window and closed is not None:
            warnings.append(f"在「{open_marker}」之前出现「{close_marker}」，已忽略。")
            index = closed
            continue

        index += 1

    if ticks and in_window:
        warnings.append(
            f"最后一次「{open_marker}」没有配上「{close_marker}」，这段没有写入粗剪。"
        )
        _add_drop(
            dropped,
            ticks,
            window_from,
            len(ticks),
            "unclosed",
            open_end,
            ticks[-1].end,
        )
    elif ticks:
        _add_drop(
            dropped,
            ticks,
            outside_from,
            len(ticks),
            "outside",
            time_at(outside_from, "start"),
            ticks[-1].end,
        )

    return MarkerResult(
        keeps=keeps,
        dropped=dropped,
        warnings=warnings,
        open_marker=open_marker,
        close_marker=close_marker,
        open_aliases=open_aliases,
        close_aliases=close_aliases,
        timing=timing_quality(transcript),
    )


def assign_timeline(spans: list[Span]) -> float:
    """把各段源时间改写成从 0 开始的成片时间。返回成片时长。"""
    cursor = 0.0
    for span in spans:
        duration = max(0.0, span.src_end - span.src_start)
        span.out_start = cursor
        span.out_end = cursor + duration
        offset = cursor - span.src_start
        for cue in span.cues:
            cue.out_start = max(span.out_start, cue.src_start + offset)
            cue.out_end = min(span.out_end, max(cue.out_start, cue.src_end + offset))
            if cue.out_end <= cue.out_start:
                cue.out_end = min(span.out_end, cue.out_start + 0.08)
        cursor += duration
    return cursor
