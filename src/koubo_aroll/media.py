"""ffmpeg：抽音频、按时间切段、拼成一条 H.264/AAC。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from koubo_aroll.errors import KouboError

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
PRESETS = {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium"}


class MediaError(KouboError):
    pass


@dataclass
class VideoInfo:
    path: Path
    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool
    rotation: int


def tool_path(name: str) -> str:
    env = os.environ.get(name.upper(), name)
    found = shutil.which(env)
    if not found:
        raise MediaError(f"找不到 {name}。请先安装 ffmpeg 并加入 PATH。")
    return found


def ffmpeg_version() -> str:
    binary = tool_path("ffmpeg")
    proc = subprocess.run([binary, "-version"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise MediaError("无法运行 ffmpeg -version。")
    return (proc.stdout or "").splitlines()[0].strip()


def run_ffmpeg(args: list[str], what: str) -> None:
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-2000:]
        raise MediaError(f"{what}失败。\n{tail}")


def even(value: int) -> int:
    value = max(2, int(value))
    return value if value % 2 == 0 else value - 1


def oriented_size(width: int, height: int, rotation: int) -> tuple[int, int]:
    if abs(int(rotation)) % 180 == 90:
        width, height = height, width
    return even(width), even(height)


def _rotation(stream: dict) -> int:
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        try:
            return int(round(float(tags["rotate"])))
        except (TypeError, ValueError):
            pass
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                return int(round(float(side["rotation"])))
            except (TypeError, ValueError):
                pass
    return 0


def _fps(stream: dict) -> float:
    raw = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/0"
    try:
        num, den = str(raw).split("/")
        den_f = float(den)
        value = float(num) / den_f if den_f else 0.0
    except (ValueError, ZeroDivisionError):
        value = 0.0
    rounded = int(round(value)) if value else 0
    if rounded < 20 or rounded > 60:
        return 30.0
    return float(rounded)


def natural_key(name: str) -> list[object]:
    import re

    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def list_videos(folder: Path) -> list[Path]:
    if not folder.is_dir():
        raise MediaError(f"素材文件夹不存在：{folder}")
    files = [
        path
        for path in folder.iterdir()
        if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in VIDEO_EXTS
    ]
    return sorted(files, key=lambda path: natural_key(path.name))


def probe(path: Path) -> VideoInfo:
    binary = tool_path("ffprobe")
    proc = subprocess.run(
        [
            binary,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise MediaError(f"无法读取视频信息：{path.name}\n{(proc.stderr or '')[-800:]}")
    data = json.loads(proc.stdout or "{}")
    video = None
    has_audio = False
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "audio":
            has_audio = True
        if stream.get("codec_type") == "video" and video is None:
            if (stream.get("disposition") or {}).get("attached_pic") == 1:
                continue
            video = stream
    if video is None:
        raise MediaError(f"文件里没有视频画面：{path.name}")
    fmt = data.get("format") or {}
    duration = float(fmt.get("duration") or video.get("duration") or 0)
    rotation = _rotation(video)
    width, height = oriented_size(int(video.get("width") or 0), int(video.get("height") or 0), rotation)
    return VideoInfo(path, width, height, _fps(video), duration, has_audio, rotation)


def extract_audio(video: Path, wav_path: Path) -> None:
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            tool_path("ffmpeg"),
            "-y",
            "-i",
            str(video),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(wav_path),
        ],
        f"抽取音频 {video.name}",
    )


def cut_segment(
    src: Path,
    dest: Path,
    start: float,
    end: float,
    *,
    width: int,
    height: int,
    fps: float,
    has_audio: bool,
    crf: int = 20,
    preset: str = "veryfast",
) -> None:
    if preset not in PRESETS:
        raise MediaError(f"不支持的编码预设：{preset}")
    duration = end - start
    if duration <= 0.04:
        raise MediaError(f"切片太短：{src.name} {start:.3f}-{end:.3f}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    preroll = 0.0 if start < 0.25 else 1.0
    seek = max(0.0, start - preroll)
    rel_start = start - seek
    rel_end = end - seek
    video_chain = (
        f"[0:v]trim=start={rel_start:.3f}:end={rel_end:.3f},setpts=PTS-STARTPTS,"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=bicubic,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"fps={fps:.0f},format=yuv420p,setsar=1[v]"
    )
    fade = ""
    if duration > 0.08:
        fade_d = 0.012
        fade = (
            f",afade=t=in:st=0:d={fade_d:.3f},"
            f"afade=t=out:st={max(0.0, duration - fade_d):.3f}:d={fade_d:.3f}"
        )
    if has_audio:
        audio_chain = (
            f"[0:a]atrim=start={rel_start:.3f}:end={rel_end:.3f},asetpts=PTS-STARTPTS,"
            f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo{fade}[a]"
        )
        cmd = [
            tool_path("ffmpeg"),
            "-y",
            "-ss",
            f"{seek:.3f}",
            "-i",
            str(src),
            "-filter_complex",
            f"{video_chain};{audio_chain}",
            "-map",
            "[v]",
            "-map",
            "[a]",
        ]
    else:
        audio_chain = (
            f"[1:a]atrim=start=0:end={duration:.3f},asetpts=PTS-STARTPTS,"
            f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo{fade}[a]"
        )
        cmd = [
            tool_path("ffmpeg"),
            "-y",
            "-ss",
            f"{seek:.3f}",
            "-i",
            str(src),
            "-f",
            "lavfi",
            "-t",
            f"{duration:.3f}",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-filter_complex",
            f"{video_chain};{audio_chain}",
            "-map",
            "[v]",
            "-map",
            "[a]",
        ]
    cmd.extend(
        [
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            "-video_track_timescale",
            "30000",
            str(dest),
        ]
    )
    run_ffmpeg(cmd, f"切出 {src.name} {start:.2f}-{end:.2f}")


def concat_segments(parts: list[Path], dest: Path) -> None:
    if not parts:
        raise MediaError("没有可拼接的片段。")
    dest.parent.mkdir(parents=True, exist_ok=True)
    list_path = dest.with_name(f".{dest.stem}.concat.txt")
    lines = []
    for part in parts:
        escaped = str(part.resolve()).replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        run_ffmpeg(
            [
                tool_path("ffmpeg"),
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(dest),
            ],
            "拼接粗剪",
        )
    finally:
        list_path.unlink(missing_ok=True)


def extract_frame(video: Path, at: float, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            tool_path("ffmpeg"),
            "-y",
            "-ss",
            f"{max(0.0, at):.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-update",
            "1",
            str(dest),
        ],
        f"截取画面 {video.name}",
    )


def sample_rgb(
    video: Path,
    at: float,
    x: int,
    y: int,
    width: int | None = None,
    height: int | None = None,
) -> tuple[int, int, int]:
    if width is None or height is None:
        info = probe(video)
        width, height = info.width, info.height
    proc = subprocess.run(
        [
            tool_path("ffmpeg"),
            "-v",
            "error",
            "-ss",
            f"{max(0.0, at):.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise MediaError(f"无法采样画面：{video.name}")
    x = min(max(0, x), width - 1)
    y = min(max(0, y), height - 1)
    index = (y * width + x) * 3
    pixel = proc.stdout[index : index + 3]
    if len(pixel) < 3:
        raise MediaError(f"画面数据不完整：{video.name}")
    return pixel[0], pixel[1], pixel[2]


def find_cjk_font() -> str | None:
    candidates = [
        os.environ.get("KOUBO_FONT", ""),
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None
