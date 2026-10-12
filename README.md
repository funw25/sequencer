# 多通道音序器（PC 端上位机）

基于 CH32V 系列 MCU 的模块化合成器系统的 PC 端音序软件。当前版本可编辑、播放、保存工程，
并把音符以 1 字节编码发往串口。硬件侧的串口接收中断尚未实现，因此实际收发未经验证。

## 当前功能

- 多通道音序编辑，通道可增删改；一个通道对应一个合成器模块或乐器
- 钢琴卷帘（`QGraphicsView` / `QGraphicsScene`）：显示网格、点击或拖动创建音符、拖动移位、拖右边缘改长度，全部吸附到整数网格
- 音符字段：音高（1~63，0 表示休止或无音）、开始位置、持续时间；开始位置与持续时间都是「网格步进的整数个数」
- 工程级 BPM、拍号、网格步进（1/1、1/2、1/4、1/8、1/16 拍）
- 播放 / 暂停 / 停止，播放头随播放移动
- 播放产生统一的 `NOTE_ON` / `NOTE_OFF` 事件，交给输出适配层
- 输出适配层有两个：`DebugOutput` 写侧栏事件日志，`SerialOutput` 发串口
- 界面左侧串口面板：扫描并选择端口、选择波特率、打开或关闭、发测试音
- 工程保存/打开，JSON 文件，`format_version: 1`

## 串口编码

1 个字节表示一个音符。硬件侧的接收中断待实现，编码细节见同目录的 `串口协议V1草案.md`。

| 位 | 含义 |
|---|---|
| bit7-6 | 通道号，0 或 1 |
| bit5-0 | 音高，1 至 63；填 0 表示该通道停止发声 |

| 通道 | 音高 | 字节 |
|---|---|---|
| 1 | 10 | `0x4A` |
| 0 | 0 | `0x00` |
| 1 | 63 | `0x7F` |

打开串口时会先发 `00 40`，把两个通道置为静音，避免上次残留的发声状态。
波特率默认 115200，下拉框内可选 9600 等取值，最终取值待硬件侧确认。

## 运行

```
pip install -r requirements.txt
python main.py
```

## 测试

```
python -m pytest                       # 全部测试（含界面测试，需要 PySide6）
python -m pytest tests/test_model.py tests/test_timing.py tests/test_playback.py tests/test_project_io.py
                                       # 只跑非界面测试，不需要 PySide6
```

界面测试在 `QT_QPA_PLATFORM=offscreen` 下运行，不需要显示器。这一点在 `tests/test_ui_smoke.py` 里用 `os.environ.setdefault` 设置，不依赖 pytest 插件。

如果所在环境的 pytest 无法读取它自己创建的临时目录，改用：

```
python -m pytest -p no:tmpdir -p no:cacheprovider
```

`conftest.py` 提供了一个自管的 `tmp_path` 夹具顶替内置插件，测试代码本身不用改，两种跑法都能通过。

当前测试数量为 106 项，全部通过。

## 目录结构

```
sequencer/
├─ requirements.txt
├─ pytest.ini
├─ conftest.py                # 自管 tmp_path 夹具，见「测试」
├─ README.md
├─ main.py                    # 启动入口：python main.py
├─ sequencer/
│  ├─ __init__.py
│  ├─ model/                  # 纯数据层
│  │  ├─ note.py              # Note：音高 / 开始位置 / 持续时间 + 字段校验
│  │  ├─ channel.py           # Channel：编号、名称、颜色、音符集合（允许重叠）
│  │  └─ project.py           # Project：BPM、拍号、网格步进、通道集合
│  ├─ core/
│  │  ├─ timing.py            # 网格整数 <-> 节拍 <-> 秒；网格吸附
│  │  └─ playback.py          # 事件展平、排序、播放引擎（play / pause / stop）
│  ├─ io/
│  │  └─ project_io.py        # 工程 JSON 保存 / 读取
│  ├─ output/                 # 输出适配层
│  │  ├─ base.py              # OutputAdapter 接口（只有这一个接口）
│  │  ├─ debug_output.py      # 事件格式化到界面日志
│  │  └─ serial_output.py     # 1 字节编码与发送，见「串口编码」
│  └─ ui/
│     ├─ main_window.py       # 菜单、通道面板、编辑区、播放控制、事件日志、串口面板
│     ├─ piano_roll.py        # 钢琴卷帘视图 + 网格 + 播放头 + 鼠标编辑
│     └─ app.py               # 入口 main()
└─ tests/
   ├─ test_model.py
   ├─ test_timing.py
   ├─ test_playback.py
   ├─ test_project_io.py
   └─ test_ui_smoke.py        # 额外加的界面冒烟测试，见「与目标结构的偏差」
```

## 关键约定

### 1. 音序编辑器不直接操作串口

```
钢琴卷帘 / 工程数据
        │
        ▼
PlaybackEngine（core/playback.py）──► NoteEvent(kind, channel_index, pitch, time_seconds)
        │
        ▼
OutputAdapter（output/base.py）
   ├─ DebugOutput   写界面事件日志（播放时启用）
   └─ SerialOutput  发串口（1 字节编码，见「串口编码」）
```

播放引擎只产生事件，不关心事件被送到哪里。接硬件时只需改 `SerialOutput.encode_event()`，
编辑器和播放引擎都不用改。

### 2. 时间单位

| 概念 | 单位 | 说明 |
| --- | --- | --- |
| `Note.start` | 网格整数 | 从 0 开始计数，不在数据里存秒 |
| `Note.duration` | 网格整数 | 至少 1 |
| `grid_step` | 每拍切几份 | 1=1/1 拍，4=1/4 拍，16=1/16 拍 |
| 一拍 | 秒 | `60 / bpm`，一律以四分音符为一拍 |
| 一格 | 秒 | `60 / bpm / grid_step`，只在播放时换算 |

拍号目前不参与时长换算，只保存在工程里并显示在状态栏。

### 3. 同一时刻的事件排序

事件按 `(时间, 类型, 通道, 音高)` 排序，其中同一时刻 `NOTE_OFF` 排在 `NOTE_ON` 之前，
避免同音高的音被自己的 `NOTE_ON` 打断。这个顺序是确定性的，测试有覆盖。

### 4. 播放位置与悬挂音

播放位置取自 `time.monotonic()`，`QTimer` 只负责「多久看一眼」，所以定时器抖动不影响时间点。

- 暂停：为正在发声的音符补发 `NOTE_OFF`，避免悬挂音；恢复播放时从暂停位置继续
- 停止：补发 `NOTE_OFF` 并回到工程起点
- 从中途起播（`collect_events(start_step=...)`）：起步早于起播点的音符只发 `NOTE_OFF`，
  不再补发已经错过的 `NOTE_ON`

### 5. 重叠音符

同一通道内允许音符在时间上重叠，也允许同音高重叠。数据层不做合并、不做冲突检查。
事件层同样不合并：两个重叠的同音高音符会产生两组 `NOTE_ON` / `NOTE_OFF`。
硬件侧每个通道同一时刻只发一个音，后触发会覆盖前一音，具体处理方式待确认。

### 6. 待确认项（代码里已用常量集中定义）

| 待确认 | 位置 | 当前取值 |
| --- | --- | --- |
| 通道编号从 0 开始还是 1 开始 | `sequencer/model/channel.py` 的 `CHANNEL_INDEX_BASE` | `0`（界面列表显示为 1.、2.…便于人读，与数据编号不同） |
| 音高集合与频率映射 | 硬件确定，PC 端不做换算 | 有效音高 1~63，0 为休止 |
| 串口波特率 | `sequencer/output/serial_output.py` 的 `DEFAULT_BAUDRATE` | `115200`，下拉框可选 9600 等；固件源码中另有 9600 的取值，待确认 |
| 音符关闭的表达 | `sequencer/output/serial_output.py` 的 `encode_event()` | 发送通道号加音高 0，即停止该通道 |
| 拍号是否影响 BPM 基准 | `sequencer/core/timing.py` | 当前不影响，BPM 按四分音符计 |

## 工程文件格式

```json
{
  "format_version": 1,
  "bpm": 120.0,
  "time_signature_numerator": 4,
  "time_signature_denominator": 4,
  "grid_step": 4,
  "channels": [
    {
      "index": 0,
      "name": "通道 0",
      "color": "#4C8BF5",
      "notes": [
        {"pitch": 60, "start": 0, "duration": 4}
      ]
    }
  ]
}
```

`format_version` 不等于 1 时直接报错，暂不实现旧版本兼容和自动迁移。
读取后的 round-trip 结果与原工程相等，测试覆盖了这一点。

## 与目标结构的偏差

1. 增加了 `tests/test_ui_smoke.py`。目标结构里没有界面测试；界面是本次交付的主要部分，
   至少需要验证窗口能构建、音符能显示、播放能把事件写进日志。测试在 offscreen 平台运行。
2. 增加了 `conftest.py`。只提供一个自管的 `tmp_path` 夹具，顶替在本环境下不可用的
   pytest 内置临时目录插件，没有引入任何第三方插件。
3. 增加了 `pytest.ini`。只设置 `testpaths = tests`。
4. 其余目录与文件名与目标结构一致，`sequencer/` 包的内部模块没有增删。
