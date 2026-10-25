"""生成带颜色和口令的演示素材，并自己核对粗剪有没有把废段剪进去。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from koubo_aroll.media import (
    MediaError,
    concat_segments,
    extract_frame,
    find_cjk_font,
    probe,
    run_ffmpeg,
    sample_rgb,
    tool_path,
)
from koubo_aroll.pipeline import Settings, run_pipeline
from koubo_aroll.stt import transcript_from_segments

# 颜色拉开距离，避免 yuv420 压缩后认错。
DROP_RGB = (192, 57, 43)
MARKER_RGB = (127, 140, 141)
DISCARD_RGB = (142, 68, 173)
KEEP_GREEN = (30, 132, 73)
KEEP_BLUE = (26, 82, 118)
KEEP_GOLD = (185, 119, 14)
UNCLOSED_RGB = (245, 176, 65)

DEMO_A = [
    {"role": "drop", "dur": 1.5, "color": "0xC0392B", "rgb": DROP_RGB, "title": "废段 · 开场口误", "kicker": "原片 · 应丢弃", "speech": "开场说错了这段不要"},
    {"role": "open", "dur": 1.0, "color": "0x7F8C8D", "rgb": MARKER_RGB, "title": "口令 · 拍错了", "kicker": "原片 · 开口令", "speech": "拍错了"},
    {"role": "discard", "dur": 2.0, "color": "0x8E44AD", "rgb": DISCARD_RGB, "title": "半句 · 重来后丢掉", "kicker": "原片 · 重来后应丢弃", "speech": "这句话只说了一半"},
    {"role": "open", "dur": 1.0, "color": "0x7F8C8D", "rgb": MARKER_RGB, "title": "口令 · 再喊拍错了", "kicker": "原片 · 开口令", "speech": "拍错了"},
    {"role": "keep", "dur": 3.6, "color": "0x1E8449", "rgb": KEEP_GREEN, "title": "保留 · 开场白", "kicker": "原片 · 应保留", "speech": "大家好，我是阿星，今天讲怎么做粗剪。"},
    {"role": "close", "dur": 1.0, "color": "0x7F8C8D", "rgb": MARKER_RGB, "title": "口令 · 可以保留", "kicker": "原片 · 收口令", "speech": "可以保留"},
    {"role": "drop", "dur": 1.5, "color": "0xC0392B", "rgb": DROP_RGB, "title": "废段 · 卡壳", "kicker": "原片 · 应丢弃", "speech": "等一下这里卡壳了"},
    {"role": "open", "dur": 1.0, "color": "0x7F8C8D", "rgb": MARKER_RGB, "title": "口令 · 拍错了", "kicker": "原片 · 开口令", "speech": "拍错了"},
    {"role": "keep", "dur": 3.0, "color": "0x1A5276", "rgb": KEEP_BLUE, "title": "保留 · 方法", "kicker": "原片 · 应保留", "speech": "正片放在两个口令中间。"},
    {"role": "close", "dur": 1.0, "color": "0x7F8C8D", "rgb": MARKER_RGB, "title": "口令 · 可以保留", "kicker": "原片 · 收口令", "speech": "可以保留"},
    {"role": "drop", "dur": 1.2, "color": "0xC0392B", "rgb": DROP_RGB, "title": "废段 · 尾音", "kicker": "原片 · 应丢弃", "speech": "尾上还有一句废话"},
]

DEMO_B = [
    {"role": "drop", "dur": 1.2, "color": "0xC0392B", "rgb": DROP_RGB, "title": "废段 · 第二段口误", "kicker": "原片 · 应丢弃", "speech": "第二段前面不要"},
    {"role": "open", "dur": 1.0, "color": "0x7F8C8D", "rgb": MARKER_RGB, "title": "口令 · 拍错了", "kicker": "原片 · 开口令", "speech": "拍错了"},
    {"role": "keep", "dur": 3.0, "color": "0xB9770E", "rgb": KEEP_GOLD, "title": "保留 · 剪映", "kicker": "原片 · 应保留", "speech": "进剪映时导入成片和字幕文件。"},
    {"role": "close", "dur": 1.0, "color": "0x7F8C8D", "rgb": MARKER_RGB, "title": "口令 · 可以保留", "kicker": "原片 · 收口令", "speech": "可以保留"},
    {"role": "open", "dur": 1.0, "color": "0x7F8C8D", "rgb": MARKER_RGB, "title": "口令 · 拍错了", "kicker": "原片 · 开口令", "speech": "拍错了"},
    {"role": "unclosed", "dur": 1.8, "color": "0xF5B041", "rgb": UNCLOSED_RGB, "title": "未闭合 · 不要进成片", "kicker": "原片 · 未闭合应丢弃", "speech": "这句忘了收口"},
]


def scenes_with_times(scenes: list[dict]) -> list[dict]:
    cursor = 0.0
    rows = []
    for scene in scenes:
        row = dict(scene)
        row["start"] = round(cursor, 3)
        cursor += float(scene["dur"])
        row["end"] = round(cursor, 3)
        rows.append(row)
    return rows


def scenes_to_transcript(scenes: list[dict], source: str) -> dict:
    timed = scenes_with_times(scenes)
    segments = []
    for scene in timed:
        segments.append(
            {
                "start": scene["start"],
                "end": scene["end"],
                "text": scene["speech"],
                "words": [
                    {"start": scene["start"], "end": scene["end"], "text": scene["speech"]}
                ],
            }
        )
    duration = timed[-1]["end"] if timed else 0.0
    return transcript_from_segments(
        segments,
        source=source,
        language="zh",
        duration=duration,
        engine="fixture",
    )


def _font_px(width: int, base: int) -> int:
    return max(18, int(round(base * width / 1280)))


def _encode_scene(
    path: Path,
    scene: dict,
    *,
    size: str,
    fps: int,
    crf: int,
    preset: str,
    burn_text: bool,
    font: str | None,
) -> None:
    width = int(size.split("x")[0])
    duration = float(scene["dur"])
    vf = "format=yuv420p"
    text_files: list[Path] = []
    if burn_text:
        if not font:
            raise MediaError("演示画面需要中文字体。可设置 KOUBO_FONT，或安装 Noto / 文泉驿 / 微软雅黑。")
        layers = []
        specs = [
            ("kicker", scene.get("kicker") or "", _font_px(width, 32), "h*0.16"),
            ("title", scene.get("title") or "", _font_px(width, 64), "(h-text_h)/2"),
            ("speech", scene.get("speech") or "", _font_px(width, 32), "h*0.74"),
        ]
        for name, text, fontsize, ypos in specs:
            if not text:
                continue
            text_path = path.parent / f"{path.stem}-{name}.txt"
            text_path.write_text(text, encoding="utf-8")
            text_files.append(text_path)
            layers.append(
                "drawtext="
                f"fontfile='{font}':textfile='{text_path}':fontsize={fontsize}:"
                "fontcolor=white:"
                f"x=(w-text_w)/2:y={ypos}:"
                "box=1:boxcolor=black@0.45:boxborderw=16"
            )
        if layers:
            vf = ",".join(layers)
    run_ffmpeg(
        [
            tool_path("ffmpeg"),
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={scene['color']}:s={size}:r={fps}:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=48000:cl=stereo:d={duration}",
            "-shortest",
            "-t",
            f"{duration:.3f}",
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            "-video_track_timescale",
            "30000",
            str(path),
        ],
        f"生成演示镜头 {path.name}",
    )
    for text_path in text_files:
        text_path.unlink(missing_ok=True)


def build_clip(
    path: Path,
    scenes: list[dict],
    *,
    size: str = "1280x720",
    fps: int = 30,
    crf: int = 23,
    preset: str = "veryfast",
    burn_text: bool = True,
    font: str | None = None,
) -> list[dict]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if font is None and burn_text:
        font = find_cjk_font()
    work = path.parent / f".{path.stem}-scenes"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    timed = scenes_with_times(scenes)
    parts: list[Path] = []
    try:
        for index, scene in enumerate(timed):
            part = work / f"scene-{index:02d}.mp4"
            _encode_scene(
                part,
                scene,
                size=size,
                fps=fps,
                crf=crf,
                preset=preset,
                burn_text=burn_text,
                font=font,
            )
            parts.append(part)
        concat_segments(parts, path)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    transcript = scenes_to_transcript(timed, path.name)
    path.with_suffix(".json").write_text(
        json.dumps(transcript, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return timed


def _near(got: tuple[int, int, int], expect: tuple[int, int, int], tol: int) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(got, expect))


def verify_keep_colors(video: Path, keeps: list[dict], color_by_text: dict[str, tuple[int, int, int]], tol: int = 36) -> None:
    info = probe(video)
    if not keeps:
        raise MediaError("粗剪没有任何保留段。")
    for keep in keeps:
        expect = color_by_text.get(keep["text"])
        if expect is None:
            raise MediaError(f"粗剪里出现了演示脚本之外的句子：{keep['text']}")
        start = float(keep["out_start"])
        end = float(keep["out_end"])
        inset = min(0.35, max(0.05, (end - start) / 4))
        points = [start + inset, (start + end) / 2, end - inset]
        for at in points:
            got = sample_rgb(video, min(at, max(0.0, info.duration - 0.08)), 8, 8, info.width, info.height)
            if not _near(got, expect, tol):
                raise MediaError(
                    f"粗剪 {at:.2f}s 的画面颜色是 {got}，不是「{keep['text']}」对应的 {expect}。"
                    "废段或口令可能被剪进成片了。"
                )


def _hstack(images: list[Path], dest: Path) -> None:
    args = [tool_path("ffmpeg"), "-y"]
    for image in images:
        args.extend(["-i", str(image)])
    filters = []
    labels = []
    for index in range(len(images)):
        filters.append(
            f"[{index}:v]scale=480:270:force_original_aspect_ratio=decrease,"
            f"pad=480:270:(ow-iw)/2:(oh-ih)/2:color=0x111111,setsar=1[s{index}]"
        )
        labels.append(f"[s{index}]")
    filt = ";".join(filters) + ";" + "".join(labels) + f"hstack=inputs={len(images)}[v]"
    args.extend(["-filter_complex", filt, "-map", "[v]", "-frames:v", "1", "-update", "1", str(dest)])
    run_ffmpeg(args, "拼接对比图")


def _vstack(images: list[Path], dest: Path) -> None:
    args = [tool_path("ffmpeg"), "-y"]
    for image in images:
        args.extend(["-i", str(image)])
    filters = [f"[{index}:v]setsar=1[v{index}]" for index in range(len(images))]
    labels = "".join(f"[v{index}]" for index in range(len(images)))
    filt = ";".join(filters) + ";" + labels + f"vstack=inputs={len(images)}[v]"
    args.extend(["-filter_complex", filt, "-map", "[v]", "-frames:v", "1", "-update", "1", str(dest)])
    run_ffmpeg(args, "上下拼接对比图")


def _bar(text: str, width: int, dest: Path, font: str) -> None:
    text_path = dest.with_suffix(".txt")
    text_path.write_text(text, encoding="utf-8")
    run_ffmpeg(
        [
            tool_path("ffmpeg"),
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x1B1F23:s={width}x88:r=1:d=1",
            "-vf",
            (
                f"drawtext=fontfile='{font}':textfile='{text_path}':fontsize=34:"
                "fontcolor=white:x=32:y=(h-text_h)/2"
            ),
            "-frames:v",
            "1",
            "-update",
            "1",
            str(dest),
        ],
        "生成对比图标题",
    )
    text_path.unlink(missing_ok=True)


def _mid(scene: dict) -> float:
    return (float(scene["start"]) + float(scene["end"])) / 2


def export_frames(
    clips: list[tuple[Path, list[dict]]],
    roughcut: Path,
    keeps: list[dict],
    frames_dir: Path,
    font: str | None,
) -> dict[str, Path]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    saved: dict[str, Path] = {}

    def grab(video: Path, at: float, name: str) -> Path:
        dest = frames_dir / name
        extract_frame(video, at, dest)
        saved[name] = dest
        return dest

    source_a, scenes_a = clips[0]
    source_b, scenes_b = clips[1]
    grab(source_a, _mid(next(s for s in scenes_a if s["role"] == "drop")), "source_drop.png")
    grab(source_a, _mid(next(s for s in scenes_a if s["role"] == "open")), "source_marker.png")
    grab(source_a, _mid(next(s for s in scenes_a if s["role"] == "discard")), "source_restart_discarded.png")
    grab(source_a, _mid(next(s for s in scenes_a if s["role"] == "keep")), "source_keep.png")
    grab(source_b, _mid(next(s for s in scenes_b if s["role"] == "unclosed")), "source_unclosed.png")
    rough_paths = []
    for index, keep in enumerate(keeps, start=1):
        at = (float(keep["out_start"]) + float(keep["out_end"])) / 2
        rough_paths.append(grab(roughcut, at, f"roughcut_keep_{index}.png"))

    if font:
        row_src = frames_dir / "_row_src.png"
        row_out = frames_dir / "_row_out.png"
        _hstack(
            [saved["source_drop.png"], saved["source_restart_discarded.png"], saved["source_marker.png"]],
            row_src,
        )
        _hstack(rough_paths[:3], row_out)
        width = 480 * 3
        bar_src = frames_dir / "_bar_src.png"
        bar_out = frames_dir / "_bar_out.png"
        bar_foot = frames_dir / "_bar_foot.png"
        _bar("原片：废段、重来半句、口令，都还在时间线上", width, bar_src, font)
        _bar("粗剪成片：只留下三句正片，口令本身也被剪掉", width, bar_out, font)
        _bar("把 roughcut.mp4 和 roughcut.srt 一起拖进剪映", width, bar_foot, font)
        sheet = frames_dir / "compare_sheet.png"
        _vstack([bar_src, row_src, bar_out, row_out, bar_foot], sheet)
        saved["compare_sheet.png"] = sheet
        for temp in (row_src, row_out, bar_src, bar_out, bar_foot):
            temp.unlink(missing_ok=True)
    return saved


def keep_color_map(*scene_groups: list[dict]) -> dict[str, tuple[int, int, int]]:
    mapping: dict[str, tuple[int, int, int]] = {}
    for scenes in scene_groups:
        for scene in scenes:
            if scene["role"] == "keep":
                mapping[scene["speech"]] = scene["rgb"]
    return mapping


def run_demo(
    output: Path,
    *,
    size: str = "1280x720",
    crf: int = 23,
    preset: str = "veryfast",
    burn_text: bool = True,
    article_dir: Path | None = None,
    make_frames: bool = True,
) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    footage = output / "footage"
    if footage.exists():
        shutil.rmtree(footage)
    footage.mkdir()
    font = find_cjk_font() if burn_text else None
    scenes_a = build_clip(
        footage / "01-开场.mp4",
        DEMO_A,
        size=size,
        crf=crf,
        preset=preset,
        burn_text=burn_text,
        font=font,
    )
    scenes_b = build_clip(
        footage / "02-方法.mp4",
        DEMO_B,
        size=size,
        crf=crf,
        preset=preset,
        burn_text=burn_text,
        font=font,
    )
    out_dir = output / "out"
    summary = run_pipeline(
        footage,
        out_dir,
        Settings(stt="json", transcript_dir=footage, crf=crf, preset=preset),
    )
    if summary.output_mp4 is None:
        raise MediaError("演示没有生成粗剪视频。")
    verify_keep_colors(summary.output_mp4, summary.keeps, keep_color_map(DEMO_A, DEMO_B))
    frames: dict[str, Path] = {}
    if make_frames:
        frames = export_frames(
            [(footage / "01-开场.mp4", scenes_a), (footage / "02-方法.mp4", scenes_b)],
            summary.output_mp4,
            summary.keeps,
            output / "frames",
            font,
        )
    if article_dir is not None:
        article_dir.mkdir(parents=True, exist_ok=True)
        for name, path in frames.items():
            if name.startswith("_"):
                continue
            shutil.copyfile(path, article_dir / name)
        shutil.copyfile(summary.output_report, article_dir / "roughcut.report.txt")
    forbidden = ("拍错了", "可以保留", "只说了一半", "忘了收口", "开场说错了", "卡壳了", "一句废话", "前面不要")
    srt = summary.output_srt.read_text(encoding="utf-8")
    leaked = [word for word in forbidden if word in srt]
    if leaked:
        raise MediaError("粗剪字幕里还留着不该保留的内容：" + "、".join(leaked))
    for sentence in keep_color_map(DEMO_A, DEMO_B):
        if sentence not in srt:
            raise MediaError(f"粗剪字幕缺少正片：{sentence}")
    return {
        "summary": summary,
        "frames": frames,
        "footage": footage,
        "out": out_dir,
    }
