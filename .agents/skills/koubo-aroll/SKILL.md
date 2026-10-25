---
name: koubo-aroll
description: >
  口播 A-roll 口令粗剪。拍摄时用「拍错了」开口、「可以保留」收口，
  只保留两句中间的内容，导出剪映可用的 roughcut.mp4 和 roughcut.srt。
  在修改口令、识别、切片、字幕或剪映导出时使用。
---

# koubo-aroll

口播素材用一对口令括出正片。不要改回「再来 / 这段OK」。

## 口令

| 说法 | 作用 |
| --- | --- |
| 拍错了 | 开口。这句之前全部丢掉，口令本身不进成片 |
| 可以保留 | 收口。口令本身不进成片，只留两句中间 |
| 窗口里又说拍错了 | 上一段未收口的内容作废，重新开口 |
| 只有开口、没有收口 | 整段不进成片，报告里警告 |
| 收口出现在开口之前 | 忽略 |

正片文案里不要出现这两个词。默认识别变体只有「拍错啦」。`--exact` 关闭变体。

## 命令

```bash
pip install -e ".[dev]"
koubo-aroll check
koubo-aroll demo -o demo_out
pytest
koubo-aroll run ./footage -o ./out
koubo-aroll run ./footage -o ./out --transcript-dir ./footage
koubo-aroll run ./footage -o ./out --stt openai
```

本地识别是 faster-whisper（`pip install -e ".[stt]"`），模型默认 `small`。付费识别读 `OPENAI_API_KEY`，可选 `OPENAI_BASE_URL` 和 `OPENAI_TRANSCRIBE_MODEL`。不要把密钥写进命令行。

## 输出

导入剪映的是：

- `roughcut.mp4`
- `roughcut.srt`（成片时间轴）

同目录还有全量 JSON/SRT、`*.filtered.json`、`*.filtered.srt`（原片时间，不要铺到成片上）和 `roughcut.report.txt`。

没有配成对的口令时退出码是 2，并且不生成 mp4。

## 改代码时

- 口令状态机在 `src/koubo_aroll/markers.py`，先改 `tests/test_markers.py`。
- 时间刀口在口令词边界。默认不要把口令音频留在成片里。
- 成片是 H.264 + AAC + yuv420p，按第一条素材的分辨率。
- 演示脚本 `koubo-aroll demo` 用色块证明保留/丢弃。废段是红，重来半句是紫，未闭合是琥珀，三句正片是绿、蓝、金。
- 跑完 `pytest`。改了切片再跑 `koubo-aroll demo -o demo_out`。
