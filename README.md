# koubo-aroll

口播 A-roll 自动粗剪。拍摄时用两个口令把正片括起来，回来得到一条粗剪 mp4 和一份 SRT，直接拖进剪映。

# 微信：**staraiwork**

## 口令

两个都要说，而且要成对。

| 你说 | 成片里 |
| --- | --- |
| **拍错了** | 开口。这句之前全部丢掉，这四个字本身也不留 |
| **可以保留** | 收口。只保留它和开口令中间的话，这四个字本身也不留 |
| 正片说到一半又喊了一次「拍错了」 | 刚才那小段作废，从新的开口重新算 |
| 只喊了「拍错了」，忘了「可以保留」 | 这段不进粗剪，报告里会警告 |
| 还没开口就喊了「可以保留」 | 忽略 |

正片里不要出现这两个词，否则会被当成口令。默认识别还接受「拍错啦」。不要用「再来」或「这段OK」。

拍摄时口令单独说清楚，前后留半拍。多条素材用文件名排序，例如 `01-开场.mp4`、`02-方法.mp4`。程序只看文件夹里的视频，不进子目录。竖屏就一路竖屏，成片跟第一条素材的分辨率走。

可以照着念：

```text
（废片随便说）
拍错了
大家好，我是阿星，今天讲怎么做粗剪。
可以保留
（卡壳）
拍错了
正片放在两个口令中间。
可以保留
```

## 安装

需要 Python 3.10+ 和 ffmpeg。

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

真人口播要本地识别时再装：

```bash
pip install -e ".[stt]"
```

Windows 若控制台中文乱码，先执行 `set PYTHONUTF8=1`。

## 运行

```bash
koubo-aroll check
koubo-aroll demo -o demo_out
koubo-aroll run ./footage -o ./out
```

`demo` 会生成两段色块素材，按口令剪出 `demo_out/out/roughcut.mp4`，并用颜色自检：红、紫、灰、琥珀不能留在成片里，只留下绿、蓝、金三段正片。

已经有字幕 JSON 时跳过识别。JSON 要和视频同名，里面是带 `start` / `end` / `text` 的 `segments`，有 `words` 时按词切，没有就在句子里按字均分时间。示例见 `fixtures/sample_transcript.json`。

```bash
koubo-aroll run ./footage -o ./out --transcript-dir ./footage
```

本地识别默认 faster-whisper 的 `small` 模型，语言中文，默认关掉 VAD，避免短口令被切掉。

```bash
koubo-aroll run ./footage -o ./out --model small
koubo-aroll run ./footage -o ./out --model medium --device cuda --compute-type float16
```

付费识别走 OpenAI 兼容接口，密钥只放环境变量：

```bash
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://api.openai.com/v1   # 可选
export OPENAI_TRANSCRIBE_MODEL=whisper-1           # 可选
koubo-aroll run ./footage -o ./out --stt openai
```

口令喷麦尾音漏进成片时，再收紧刀口：

```bash
koubo-aroll run ./footage -o ./out --head-pad 0.08 --tail-pad 0.08
```

没有配成对的口令时，命令退出码是 2，不生成 mp4。

## 输出

| 文件 | 用途 |
| --- | --- |
| `roughcut.mp4` | 粗剪成片，H.264 + AAC |
| `roughcut.srt` | 成片时间轴字幕，导入剪映用这个 |
| `roughcut.json` | 保留段的机器可读结果，含成片时间 |
| `roughcut.report.txt` | 哪句留了、哪句丢了 |
| `素材名.json` / `素材名.srt` | 识别全文，方便核对口令有没有听错 |
| `素材名.filtered.srt` | 按原片时间的保留字幕，不要铺到成片上 |

## 导入剪映

用剪映专业版（电脑版）：

1. 新建项目。分辨率、帧率跟成片一致。演示成片是 1280×720、30 帧；实拍则跟你的第一条素材走。
2. 导入 `roughcut.mp4`，拖到时间线，从头铺好。
3. 打开「文本」，选择「导入字幕」，选同一个目录里的 `roughcut.srt`。
4. 先导入字幕，再做精剪。不要同时再跑一遍智能字幕，否则会重复。
5. 字幕乱码时，确认 SRT 是 UTF-8。

手机版剪映可以导入 mp4。SRT 用电脑版更稳。

## 演示在证明什么

`koubo-aroll demo` 的原片里故意放了废段、口令、说了一半又重来的句子，以及一句忘了收口的话。粗剪里只应该剩下三句：

- 大家好，我是阿星，今天讲怎么做粗剪。
- 正片放在两个口令中间。
- 进剪映时导入成片和字幕文件。

对比图在跑完 demo 后出现在 `demo_out/frames/compare_sheet.png`。仓库里的成图在 `docs/images/`。

## 开发

```bash
pytest
```

口令规则在 `src/koubo_aroll/markers.py`。给代理看的操作说明在 `.agents/skills/koubo-aroll/SKILL.md`。

## 许可

MIT。Copyright (c) 2026 staraiwork。
