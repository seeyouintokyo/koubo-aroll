from pathlib import Path

import pytest

from koubo_aroll.cli import main
from koubo_aroll.demo import build_clip, run_demo
from koubo_aroll.media import probe


def test_demo_roughcut_keeps_only_marked_spans(tmp_path: Path):
    result = run_demo(
        tmp_path / "demo",
        size="320x180",
        crf=33,
        preset="ultrafast",
        burn_text=False,
        make_frames=False,
    )
    summary = result["summary"]
    assert summary.keep_count == 3
    assert [keep["text"] for keep in summary.keeps] == [
        "大家好，我是阿星，今天讲怎么做粗剪。",
        "正片放在两个口令中间。",
        "进剪映时导入成片和字幕文件。",
    ]
    assert summary.duration == 9.6
    info = probe(summary.output_mp4)
    assert info.duration == pytest_approx(9.6)
    assert info.width == 320
    assert info.height == 180
    srt = summary.output_srt.read_text(encoding="utf-8")
    assert srt.startswith("1\n00:00:00,")
    assert "拍错了" not in srt
    assert "可以保留" not in srt
    full = (result["out"] / "01-开场.srt").read_text(encoding="utf-8")
    assert "拍错了" in full
    assert "可以保留" in full
    filtered = (result["out"] / "01-开场.filtered.srt").read_text(encoding="utf-8")
    assert "拍错了" not in filtered
    assert "大家好，我是阿星，今天讲怎么做粗剪。" in filtered
    assert any("没有配上" in warning for warning in summary.warnings)
    assert not (result["out"] / ".work").exists()


def test_run_exits_2_when_no_pair(tmp_path: Path):
    footage = tmp_path / "footage"
    footage.mkdir()
    build_clip(
        footage / "only.mp4",
        [
            {
                "role": "drop",
                "dur": 0.6,
                "color": "0xC0392B",
                "rgb": (192, 57, 43),
                "title": "废段",
                "speech": "没有口令",
            }
        ],
        size="320x180",
        crf=33,
        preset="ultrafast",
        burn_text=False,
    )
    out = tmp_path / "out"
    code = main(["run", str(footage), "-o", str(out), "--stt", "json", "--crf", "33", "--preset", "ultrafast"])
    assert code == 2
    assert not (out / "roughcut.mp4").exists()
    report = (out / "roughcut.report.txt").read_text(encoding="utf-8")
    assert "没有配成对" in report


def test_check_and_help():
    assert main(["check"]) == 0
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0


def pytest_approx(value: float):
    import pytest

    return pytest.approx(value, abs=0.35)
