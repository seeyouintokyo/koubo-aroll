from koubo_aroll.markers import find_keeps
from koubo_aroll.media import even, natural_key, oriented_size
from koubo_aroll.srt import format_timestamp, render_srt
from koubo_aroll.stt import load_transcript, transcript_from_openai


def test_srt_timestamp_and_order():
    assert format_timestamp(1.234) == "00:00:01,234"
    assert format_timestamp(3661.009) == "01:01:01,009"
    from koubo_aroll.markers import Cue

    text = render_srt(
        [
            Cue(0, 1, "后说的", out_start=1.2, out_end=2.0),
            Cue(0, 1, "先被推后的", out_start=0.2, out_end=0.4),
        ]
    )
    assert "00:00:01,200 --> 00:00:02,000" in text
    assert text.strip().startswith("1\n")
    assert "\n\n2\n" in text


def test_openai_payload_feeds_marker_cut():
    payload = {
        "language": "chinese",
        "duration": 3,
        "text": "拍错了你好可以保留",
        "segments": [{"id": 0, "start": 0.0, "end": 3.0, "text": "拍错了你好可以保留"}],
        "words": [
            {"word": "拍错了", "start": 0.0, "end": 1.0},
            {"word": "你好", "start": 1.0, "end": 2.0},
            {"word": "可以保留", "start": 2.0, "end": 3.0},
        ],
    }
    transcript = transcript_from_openai(payload, "a.mp4", engine="openai:whisper-1")
    result = find_keeps(transcript)
    assert transcript["engine"] == "openai:whisper-1"
    assert result.keeps[0].text == "你好"
    assert result.keeps[0].src_start == 1
    assert result.keeps[0].src_end == 2


def test_load_transcript_accepts_a_bare_list(tmp_path):
    path = tmp_path / "clip.json"
    path.write_text(
        '[{"start": 0, "end": 1, "text": "拍错了"}, {"start": 1, "end": 2, "text": "留"}, {"start": 2, "end": 3, "text": "可以保留"}]',
        encoding="utf-8",
    )
    transcript = load_transcript(path, "clip.mp4")
    result = find_keeps(transcript)
    assert result.keeps[0].text == "留"


def test_sort_and_rotation_helpers():
    names = ["10-结尾.mp4", "2-中段.MOV", "1-开场.mp4"]
    assert sorted(names, key=natural_key) == ["1-开场.mp4", "2-中段.MOV", "10-结尾.mp4"]
    assert oriented_size(1920, 1080, -90) == (1080, 1920)
    assert oriented_size(1080, 1920, 0) == (1080, 1920)
    assert even(1919) == 1918
