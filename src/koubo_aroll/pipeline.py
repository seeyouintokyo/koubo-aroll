"""从素材文件夹到粗剪 mp4 + SRT。"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from koubo_aroll.errors import KouboError
from koubo_aroll.markers import (
    DEFAULT_CLOSE,
    DEFAULT_OPEN,
    Span,
    assign_timeline,
    find_keeps,
    resolve_aliases,
)
from koubo_aroll.media import (
    VideoInfo,
    concat_segments,
    cut_segment,
    extract_audio,
    list_videos,
    probe,
)
from koubo_aroll.srt import format_clock, render_srt, segments_to_srt
from koubo_aroll.stt import (
    SttError,
    build_initial_prompt,
    load_transcript,
    transcribe_openai,
    transcribe_whisper,
)

ROUGHCUT_MP4 = "roughcut.mp4"
ROUGHCUT_SRT = "roughcut.srt"
ROUGHCUT_JSON = "roughcut.json"
ROUGHCUT_REPORT = "roughcut.report.txt"

REASON_ZH = {
    "outside": "口令之外",
    "restarted": "重来作废",
    "unclosed": "未闭合",
    "empty": "空片段",
    "too_short": "太短",
}


class PipelineError(KouboError):
    pass


@dataclass
class Settings:
    open_marker: str = DEFAULT_OPEN
    close_marker: str = DEFAULT_CLOSE
    open_extra: tuple[str, ...] = ()
    close_extra: tuple[str, ...] = ()
    exact: bool = False
    head_pad: float = 0.0
    tail_pad: float = 0.0
    min_keep: float = 0.2
    cue_chars: int = 16
    cue_gap: float = 0.6
    stt: str = "whisper"
    model: str = "small"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str = "zh"
    vad: bool = False
    transcript_dir: Path | None = None
    crf: int = 20
    preset: str = "veryfast"
    keep_temp: bool = False
    openai_model: str = "whisper-1"


@dataclass
class _Piece:
    source: Path
    info: VideoInfo
    span: Span


@dataclass
class RunSummary:
    keep_count: int
    drop_count: int
    warnings: list[str]
    duration: float
    output_mp4: Path | None
    output_srt: Path
    output_json: Path
    output_report: Path
    keeps: list[dict] = field(default_factory=list)
    clips: list[dict] = field(default_factory=list)


def _dump(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _span_dict(source_name: str, span: Span) -> dict:
    return {
        "source": source_name,
        "src_start": round(span.src_start, 3),
        "src_end": round(span.src_end, 3),
        "out_start": round(span.out_start, 3),
        "out_end": round(span.out_end, 3),
        "text": span.text,
        "cues": [
            {
                "src_start": round(cue.src_start, 3),
                "src_end": round(cue.src_end, 3),
                "out_start": round(cue.out_start, 3),
                "out_end": round(cue.out_end, 3),
                "text": cue.text,
            }
            for cue in span.cues
        ],
    }


def _drop_dict(item) -> dict:
    return {
        "reason": item.reason,
        "reason_zh": REASON_ZH.get(item.reason, item.reason),
        "src_start": round(item.src_start, 3),
        "src_end": round(item.src_end, 3),
        "text": item.text,
    }


def _validate(settings: Settings) -> None:
    if settings.stt not in {"whisper", "openai", "json"}:
        raise PipelineError("识别方式只能是 whisper、openai 或 json。")
    if settings.head_pad < 0 or settings.tail_pad < 0:
        raise PipelineError("head-pad 和 tail-pad 不能为负数。")
    if settings.head_pad > 0.5 or settings.tail_pad > 0.5:
        raise PipelineError("pad 最大 0.5 秒，避免把正片切掉。")
    if settings.min_keep < 0:
        raise PipelineError("min-keep 不能为负数。")
    if not 0 <= settings.crf <= 51:
        raise PipelineError("crf 需要在 0 到 51 之间。")


def _transcribe_one(
    video: Path,
    wav_path: Path,
    settings: Settings,
    open_marker: str,
    close_marker: str,
) -> dict:
    if settings.stt == "json":
        directory = settings.transcript_dir or video.parent
        json_path = directory / f"{video.stem}.json"
        if not json_path.is_file():
            raise PipelineError(f"找不到与视频同名的字幕 JSON：{json_path}")
        return load_transcript(json_path, video.name)
    if not wav_path.is_file():
        extract_audio(video, wav_path)
    try:
        if settings.stt == "openai":
            return transcribe_openai(
                wav_path,
                source=video.name,
                language=settings.language,
                model=settings.openai_model,
            )
        return transcribe_whisper(
            wav_path,
            source=video.name,
            model_name=settings.model,
            device=settings.device,
            compute_type=settings.compute_type,
            language=settings.language,
            initial_prompt=build_initial_prompt(open_marker, close_marker),
            vad=settings.vad,
        )
    except SttError as exc:
        raise PipelineError(str(exc)) from exc


def render_report(summary: dict) -> str:
    lines = [
        "口播粗剪报告",
        f"开口令：{summary['open_marker']}",
        f"收口令：{summary['close_marker']}",
        f"别名：开口 {' / '.join(summary['open_aliases'])}；收口 {' / '.join(summary['close_aliases'])}",
        f"保留 {summary['keep_count']} 段，丢弃 {summary['drop_count']} 段，成片 {format_clock(summary['duration'])}",
        "",
    ]
    if summary["keep_count"] == 0:
        lines.append("没有配成对的口令，所以没有生成粗剪视频。")
        lines.append("拍摄时先说开口令，正片说完再说收口令。")
        lines.append("")
    for clip in summary["clips"]:
        lines.append(f"[{clip['source']}]")
        if not clip["keeps"] and not clip["dropped"]:
            lines.append("  （没有识别到文本）")
        for keep in clip["keeps"]:
            lines.append(
                f"  保留 {format_clock(keep['src_start'])} → {format_clock(keep['src_end'])}  {keep['text']}"
            )
        for item in clip["dropped"]:
            lines.append(
                f"  丢弃·{item['reason_zh']} {format_clock(item['src_start'])} → {format_clock(item['src_end'])}  {item['text']}"
            )
        for warning in clip["warnings"]:
            lines.append(f"  警告 {warning}")
        lines.append("")
    lines.append("导入剪映时，请使用成片旁的 roughcut.srt，不要用单条素材的 filtered.srt。")
    lines.append("")
    return "\n".join(lines)


def run_pipeline(input_dir: Path, output_dir: Path, settings: Settings) -> RunSummary:
    _validate(settings)
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    if output_dir == input_dir or input_dir in output_dir.parents:
        raise PipelineError("输出目录不要放在素材目录里面，避免把成片又当成素材。")
    videos = list_videos(input_dir)
    if not videos:
        raise PipelineError(f"{input_dir} 里没有 mp4/mov/mkv 等视频。")
    stems = [video.stem for video in videos]
    if len(set(stems)) != len(stems):
        raise PipelineError("有两段素材去掉扩展名后重名，请改名后再跑。")

    open_aliases, close_aliases = resolve_aliases(
        settings.open_marker,
        settings.close_marker,
        open_extra=settings.open_extra,
        close_extra=settings.close_extra,
        exact=settings.exact,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    work = output_dir / ".work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    pieces: list[_Piece] = []
    clip_payloads: list[dict] = []
    warnings: list[str] = []
    try:
        for video in videos:
            info = probe(video)
            print(f"处理 {video.name}（{info.duration:.1f}s）")
            transcript = _transcribe_one(
                video,
                work / f"{video.stem}.wav",
                settings,
                settings.open_marker,
                settings.close_marker,
            )
            transcript["source"] = video.name
            _dump(output_dir / f"{video.stem}.json", transcript)
            (output_dir / f"{video.stem}.srt").write_text(
                segments_to_srt(transcript["segments"]),
                encoding="utf-8",
            )
            result = find_keeps(
                transcript,
                open_marker=settings.open_marker,
                close_marker=settings.close_marker,
                open_aliases=open_aliases,
                close_aliases=close_aliases,
                head_pad=settings.head_pad,
                tail_pad=settings.tail_pad,
                min_keep=settings.min_keep,
                cue_chars=settings.cue_chars,
                cue_gap=settings.cue_gap,
            )
            for span in result.keeps:
                if span.src_end > info.duration + 0.08:
                    warnings.append(f"{video.name} 的保留段超出视频时长，已截到结尾。")
                    span.src_end = max(span.src_start + 0.05, info.duration)
            for warning in result.warnings:
                warnings.append(f"{video.name}：{warning}")
            for span in result.keeps:
                pieces.append(_Piece(video, info, span))
            clip_payloads.append(
                {
                    "source": video.name,
                    "timing": result.timing,
                    "keeps": [],
                    "dropped": [_drop_dict(item) for item in result.dropped],
                    "warnings": list(result.warnings),
                }
            )

        duration = assign_timeline([piece.span for piece in pieces])
        keep_rows = [_span_dict(piece.source.name, piece.span) for piece in pieces]
        for clip in clip_payloads:
            clip["keeps"] = [row for row in keep_rows if row["source"] == clip["source"]]
            stem = Path(clip["source"]).stem
            _dump(
                output_dir / f"{stem}.filtered.json",
                {
                    "source": clip["source"],
                    "open_marker": settings.open_marker,
                    "close_marker": settings.close_marker,
                    "timing": clip["timing"],
                    "warnings": clip["warnings"],
                    "keeps": clip["keeps"],
                    "dropped": clip["dropped"],
                },
            )
            clip_cues = []
            for piece in pieces:
                if piece.source.name == clip["source"]:
                    clip_cues.extend(piece.span.cues)
            (output_dir / f"{stem}.filtered.srt").write_text(
                render_srt(clip_cues, timebase="src"),
                encoding="utf-8",
            )

        output_mp4: Path | None = None
        if pieces:
            infos = [piece.info for piece in pieces]
            width = infos[0].width
            height = infos[0].height
            fps = infos[0].fps
            segment_paths: list[Path] = []
            for index, piece in enumerate(pieces, start=1):
                dest = work / f"seg-{index:03d}.mp4"
                print(
                    f"  切 {piece.source.name} "
                    f"{piece.span.src_start:.2f}-{piece.span.src_end:.2f}"
                )
                cut_segment(
                    piece.source,
                    dest,
                    piece.span.src_start,
                    piece.span.src_end,
                    width=width,
                    height=height,
                    fps=fps,
                    has_audio=piece.info.has_audio,
                    crf=settings.crf,
                    preset=settings.preset,
                )
                segment_paths.append(dest)
            output_mp4 = output_dir / ROUGHCUT_MP4
            concat_segments(segment_paths, output_mp4)
        elif (output_dir / ROUGHCUT_MP4).exists():
            (output_dir / ROUGHCUT_MP4).unlink()

        cues = [cue for piece in pieces for cue in piece.span.cues]
        srt_path = output_dir / ROUGHCUT_SRT
        srt_path.write_text(render_srt(cues, timebase="out"), encoding="utf-8")
        manifest = {
            "version": 1,
            "open_marker": settings.open_marker,
            "close_marker": settings.close_marker,
            "open_aliases": list(open_aliases),
            "close_aliases": list(close_aliases),
            "duration": round(duration, 3),
            "keep_count": len(pieces),
            "drop_count": sum(len(clip["dropped"]) for clip in clip_payloads),
            "warnings": warnings,
            "video": output_mp4.name if output_mp4 else None,
            "srt": ROUGHCUT_SRT,
            "clips": clip_payloads,
            "keeps": keep_rows,
        }
        json_path = output_dir / ROUGHCUT_JSON
        report_path = output_dir / ROUGHCUT_REPORT
        _dump(json_path, manifest)
        report_path.write_text(render_report(manifest), encoding="utf-8")
        return RunSummary(
            keep_count=len(pieces),
            drop_count=manifest["drop_count"],
            warnings=warnings,
            duration=duration,
            output_mp4=output_mp4,
            output_srt=srt_path,
            output_json=json_path,
            output_report=report_path,
            keeps=keep_rows,
            clips=clip_payloads,
        )
    finally:
        if not settings.keep_temp and work.exists():
            shutil.rmtree(work, ignore_errors=True)
