"""命令行：koubo-aroll run / demo / check。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from koubo_aroll import __version__
from koubo_aroll.errors import KouboError
from koubo_aroll.markers import DEFAULT_CLOSE, DEFAULT_OPEN
from koubo_aroll.media import PRESETS, ffmpeg_version, find_cjk_font
from koubo_aroll.pipeline import Settings, run_pipeline
from koubo_aroll.srt import format_clock
from koubo_aroll.stt import whisper_available


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass


def _cmd_check(_args=None) -> int:
    print(ffmpeg_version())
    font = find_cjk_font()
    print(f"中文字体：{font or '未找到。演示烧字需要字体，可设置 KOUBO_FONT。'}")
    print("faster-whisper：" + ("已安装" if whisper_available() else "未安装。本地识别请 pip install -e \".[stt]\""))
    print("OPENAI_API_KEY：" + ("已设置" if os.environ.get("OPENAI_API_KEY") else "未设置"))
    model = os.environ.get("OPENAI_TRANSCRIBE_MODEL", "whisper-1")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    print(f"付费识别：{base}  模型 {model}")
    print(f"默认口令：{DEFAULT_OPEN} …… {DEFAULT_CLOSE}")
    return 0


def _cmd_run(args) -> int:
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output)
    transcript_dir = Path(args.transcript_dir) if args.transcript_dir else None
    stt = args.stt or os.environ.get("KOUBO_STT", "whisper")
    if transcript_dir is not None:
        if stt != "json":
            print("已提供字幕 JSON，跳过语音识别。")
        stt = "json"
    elif stt == "json":
        transcript_dir = input_dir
    if args.preset not in PRESETS:
        raise KouboError(f"不支持的编码预设：{args.preset}")
    settings = Settings(
        open_marker=args.open_marker,
        close_marker=args.close_marker,
        open_extra=tuple(args.open_alias or ()),
        close_extra=tuple(args.close_alias or ()),
        exact=args.exact,
        head_pad=args.head_pad,
        tail_pad=args.tail_pad,
        min_keep=args.min_keep,
        cue_chars=args.cue_chars,
        stt=stt,
        model=args.model or os.environ.get("WHISPER_MODEL", "small"),
        device=args.device or os.environ.get("WHISPER_DEVICE", "cpu"),
        compute_type=args.compute_type or os.environ.get("WHISPER_COMPUTE_TYPE", "int8"),
        language=args.language,
        vad=args.vad,
        transcript_dir=transcript_dir,
        crf=args.crf,
        preset=args.preset,
        keep_temp=args.keep_temp,
        openai_model=args.openai_model or os.environ.get("OPENAI_TRANSCRIBE_MODEL", "whisper-1"),
    )
    summary = run_pipeline(input_dir, output_dir, settings)
    print("")
    print(summary.output_report.read_text(encoding="utf-8"))
    if summary.keep_count == 0:
        print("没有生成粗剪视频。")
        return 2
    print(f"成片 {summary.output_mp4}  ({format_clock(summary.duration)})")
    print(f"字幕 {summary.output_srt}")
    print("剪映请导入上面这两个文件。单条素材的 filtered.srt 用的是原片时间，不要铺到成片上。")
    return 0


def _cmd_demo(args) -> int:
    from koubo_aroll.demo import run_demo

    result = run_demo(
        Path(args.output),
        size=args.size,
        crf=args.crf,
        preset=args.preset,
        burn_text=not args.no_text,
        article_dir=Path(args.article_dir) if args.article_dir else None,
    )
    summary = result["summary"]
    print("")
    print(summary.output_report.read_text(encoding="utf-8"))
    print(f"成片 {summary.output_mp4}  ({format_clock(summary.duration)})")
    print(f"字幕 {summary.output_srt}")
    print(f"对比图 {result['frames'].get('compare_sheet.png', result['out'])}")
    print("画面核对通过：粗剪里只有保留段的颜色，废段、口令和未闭合句子都没有进去。")
    return 0


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        prog="koubo-aroll",
        description="口播 A-roll 口令粗剪。保留「拍错了」和「可以保留」中间的内容，导出剪映可用的 mp4 + SRT。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  koubo-aroll check\n"
            "  koubo-aroll demo -o demo_out\n"
            "  koubo-aroll run ./footage -o ./out\n"
            "  koubo-aroll run ./footage -o ./out --transcript-dir ./footage\n"
            "  koubo-aroll run ./footage -o ./out --stt openai\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"koubo-aroll {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    check = sub.add_parser("check", help="检查 ffmpeg、字体和识别依赖")
    check.set_defaults(func=_cmd_check)

    run = sub.add_parser("run", help="处理一个素材文件夹")
    run.add_argument("input_dir", help="A-roll 视频所在文件夹，不进入子目录")
    run.add_argument("-o", "--output", required=True, help="输出目录，不要放在素材目录里")
    run.add_argument("--stt", choices=["whisper", "openai", "json"], default=None, help="默认识别方式，也可用环境变量 KOUBO_STT")
    run.add_argument("--transcript-dir", help="与视频同名的 JSON。提供后跳过识别")
    run.add_argument("--model", default=None, help="faster-whisper 模型，默认 small，也可用 WHISPER_MODEL")
    run.add_argument("--device", default=None, help="cpu 或 cuda，也可用 WHISPER_DEVICE")
    run.add_argument("--compute-type", default=None, help="默认 int8，也可用 WHISPER_COMPUTE_TYPE")
    run.add_argument("--language", default="zh")
    run.add_argument("--vad", action="store_true", help="打开 Whisper VAD。短口令可能被切掉，默认关闭")
    run.add_argument("--openai-model", default=None, help="付费识别模型，默认 whisper-1")
    run.add_argument("--open", dest="open_marker", default=DEFAULT_OPEN, help="开口令，默认 拍错了")
    run.add_argument("--close", dest="close_marker", default=DEFAULT_CLOSE, help="收口令，默认 可以保留")
    run.add_argument("--open-alias", action="append", help="额外的开口令写法，可重复")
    run.add_argument("--close-alias", action="append", help="额外的收口令写法，可重复")
    run.add_argument("--exact", action="store_true", help="关闭默认的「拍错啦」模糊匹配")
    run.add_argument("--head-pad", type=float, default=0.0, help="开口令结束后再往后收多少秒，避开喷麦尾音")
    run.add_argument("--tail-pad", type=float, default=0.0, help="收口令开始前再提前多少秒")
    run.add_argument("--min-keep", type=float, default=0.2, help="短于这么多秒的保留段会被丢掉")
    run.add_argument("--cue-chars", type=int, default=16, help="一条字幕大约多少个字")
    run.add_argument("--crf", type=int, default=20)
    run.add_argument("--preset", default="veryfast")
    run.add_argument("--keep-temp", action="store_true", help="保留切片临时文件")
    run.set_defaults(func=_cmd_run)

    demo = sub.add_parser("demo", help="生成演示素材并跑通粗剪，用画面颜色证明保留和丢弃")
    demo.add_argument("-o", "--output", default="demo_out")
    demo.add_argument("--size", default="1280x720")
    demo.add_argument("--crf", type=int, default=23)
    demo.add_argument("--preset", default="veryfast")
    demo.add_argument("--no-text", action="store_true", help="画面不烧中文字，只留色块")
    demo.add_argument("--article-dir", help="把对比图和报告复制到这个目录")
    demo.set_defaults(func=_cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KouboError as exc:
        print(str(exc), file=sys.stderr)
        return 1
