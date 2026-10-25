from koubo_aroll.demo import DEMO_A, DEMO_B, scenes_to_transcript
from koubo_aroll.markers import assign_timeline, display_text, find_keeps


def _seg(start, end, text, words=None):
    segment = {"start": start, "end": end, "text": text}
    if words is not None:
        segment["words"] = words
    return {"segments": [segment]} if False else segment


def transcript(*segments):
    return {"language": "zh", "segments": list(segments)}


def test_pair_keeps_only_the_middle():
    result = find_keeps(
        transcript(
            _seg(0, 2, "开场说错了这段不要"),
            _seg(2, 3, "拍错了"),
            _seg(3, 6, "你好世界"),
            _seg(6, 7, "可以保留"),
            _seg(7, 9, "尾上还有一句废话"),
        )
    )
    assert [span.text for span in result.keeps] == ["你好世界"]
    assert result.keeps[0].src_start == 3
    assert result.keeps[0].src_end == 6
    assert "拍错了" not in result.keeps[0].text
    assert "可以保留" not in result.keeps[0].text
    assert any("开场说错了" in item.text for item in result.dropped)
    assert any("一句废话" in item.text for item in result.dropped)


def test_second_open_restarts_the_window():
    result = find_keeps(
        transcript(
            _seg(0, 1, "拍错了"),
            _seg(1, 3, "这句话只说了一半"),
            _seg(3, 4, "拍错了"),
            _seg(4, 7, "正式内容"),
            _seg(7, 8, "可以保留"),
        )
    )
    assert [span.text for span in result.keeps] == ["正式内容"]
    assert any(item.reason == "restarted" and "只说了一半" in item.text for item in result.dropped)
    assert any("重新开始" in warning for warning in result.warnings)


def test_unclosed_open_is_dropped():
    result = find_keeps(
        transcript(
            _seg(0, 1, "拍错了"),
            _seg(1, 3, "这句忘了收口"),
        )
    )
    assert result.keeps == []
    assert any(item.reason == "unclosed" and "忘了收口" in item.text for item in result.dropped)
    assert any("没有配上" in warning for warning in result.warnings)


def test_close_before_open_is_ignored():
    result = find_keeps(
        transcript(
            _seg(0, 1, "可以保留"),
            _seg(1, 2, "拍错了"),
            _seg(2, 4, "留下"),
            _seg(4, 5, "可以保留"),
        )
    )
    assert [span.text for span in result.keeps] == ["留下"]
    assert any("之前出现" in warning for warning in result.warnings)


def test_marker_split_across_segments_and_punctuation():
    result = find_keeps(
        transcript(
            _seg(0, 1, "拍，错"),
            _seg(1, 2, "了后面开始"),
            _seg(2, 4, "可以保留"),
        )
    )
    assert result.keeps[0].text == "后面开始"
    assert result.keeps[0].src_start == 1.2


def test_la_variant_and_brackets():
    result = find_keeps(
        transcript(
            _seg(0, 1, "「拍错啦」"),
            _seg(1, 2, "可以"),
            _seg(2, 3, "可以保留了"),
        )
    )
    assert [span.text for span in result.keeps] == ["可以"]


def test_word_timestamps_cut_on_marker_edges():
    result = find_keeps(
        transcript(
            _seg(
                0,
                3,
                "拍错了你好可以保留",
                words=[
                    {"start": 0.0, "end": 1.0, "text": "拍错了"},
                    {"start": 1.2, "end": 2.0, "text": "你好"},
                    {"start": 2.0, "end": 3.0, "text": "可以保留"},
                ],
            )
        )
    )
    assert result.timing == "word"
    assert result.keeps[0].text == "你好"
    assert result.keeps[0].src_start == 1.0
    assert result.keeps[0].src_end == 2.0


def test_custom_markers_do_not_add_default_alias():
    result = find_keeps(
        transcript(
            _seg(0, 1, "拍错了"),
            _seg(1, 2, "不要因为默认口令留下"),
            _seg(2, 3, "开工"),
            _seg(3, 4, "这句算数"),
            _seg(4, 5, "收工"),
        ),
        open_marker="开工",
        close_marker="收工",
        exact=True,
    )
    assert [span.text for span in result.keeps] == ["这句算数"]


def test_exact_disables_la_variant():
    result = find_keeps(
        transcript(
            _seg(0, 1, "拍错啦"),
            _seg(1, 2, "这句不该留"),
            _seg(2, 3, "可以保留"),
        ),
        exact=True,
    )
    assert result.keeps == []


def test_empty_pair_is_skipped():
    result = find_keeps(transcript(_seg(0, 1, "拍错了可以保留")))
    assert result.keeps == []
    assert any("没有足够内容" in warning for warning in result.warnings)


def test_timeline_is_continuous_across_spans():
    result = find_keeps(
        transcript(
            _seg(0, 1, "拍错了"),
            _seg(1, 3, "第一句"),
            _seg(3, 4, "可以保留"),
            _seg(10, 11, "拍错了"),
            _seg(11, 14, "第二句"),
            _seg(14, 15, "可以保留"),
        )
    )
    duration = assign_timeline(result.keeps)
    assert duration == 5
    assert (result.keeps[0].out_start, result.keeps[0].out_end) == (0, 2)
    assert (result.keeps[1].out_start, result.keeps[1].out_end) == (2, 5)
    assert result.keeps[0].cues[0].out_start == 0


def test_long_line_splits_subtitles_without_markers():
    sentence = "一二三四五六七八九十，甲乙丙丁戊己庚辛壬癸，子丑寅卯辰巳午未申酉。"
    result = find_keeps(
        transcript(
            _seg(0, 1, "拍错了"),
            _seg(1, 8, sentence),
            _seg(8, 9, "可以保留"),
        ),
        cue_chars=12,
    )
    assert len(result.keeps[0].cues) >= 2
    merged = "".join(cue.text for cue in result.keeps[0].cues)
    assert "拍错了" not in merged
    assert "可以保留" not in merged
    assert "一二三四五六七八九十" in merged


def test_demo_script_keeps_three_sentences():
    first = find_keeps(scenes_to_transcript(DEMO_A, "01-开场.mp4"))
    second = find_keeps(scenes_to_transcript(DEMO_B, "02-方法.mp4"))
    texts = [span.text for span in first.keeps + second.keeps]
    assert texts == [
        "大家好，我是阿星，今天讲怎么做粗剪。",
        "正片放在两个口令中间。",
        "进剪映时导入成片和字幕文件。",
    ]
    blob = "".join(texts)
    for banned in ("拍错了", "可以保留", "只说了一半", "忘了收口"):
        assert banned not in blob
    assert any(item.reason == "unclosed" for item in second.dropped)
    assert any(item.reason == "restarted" for item in first.dropped)


def test_display_text_removes_spaces_between_chinese():
    assert display_text("你 好， 世界") == "你好，世界"
    assert display_text("hello world") == "hello world"
