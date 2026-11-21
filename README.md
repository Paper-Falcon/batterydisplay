# BatteryDisplay

无线键鼠的电量，直接显示在 Windows 任务栏上——**不用打开任何厂商驱动**。

![任务栏效果](docs/images/taskbar.png)

上图是真实截图（透明任务栏 + 动态壁纸）：键盘 88%、鼠标 58%。

启动瞬间或设备休眠读不到时，显示占位并降低透明度，不会留一片空白：

![占位状态](docs/images/taskbar-loading.png)

---

## 为什么做这个

罗技要开 G HUB、雷蛇要开 Synapse、国产外设要开各自的驱动，才看得到电量。
而 Windows 本身**没有统一的接口**能读取无线外设电量：BLE、经典蓝牙、2.4G 私有接收器、
有线 HID，四条路的取数方式完全不同，同一个品牌的不同型号也可能不一样。

这个项目的做法是：**把每台设备的协议描述成一份 JSON，用一个通用引擎去执行**。
加新设备 = 加一个配置文件，不改一行代码。

---

## 支持情况

| 设备 | 连接 | 协议 | 状态 |
|---|---|---|---|
| ATK 蜻蜓 A9 Plus | 2.4G `373B:1216` | 厂商自定义 HID，usage page `0xFF05` | ✅ 已与 ATK HUB 网页驱动交叉验证 |
| 狼蛛 F108 Pro | 2.4G `05AC:024F` | SONiX 协议族，`0xFF60` / 33 字节帧 | ✅ 三次稳定读数 |
| 重力星球键盘 | 2.4G `3554:FA09` | — | ❌ 该接收器不提供电量 |
| 杰理方案耳机 | 2.4G `4C4A:4155` | — | ❌ 无厂商通道 |

后两台不是"还没做"，是**实测确认读不到**，证据记录在
[`device-packs/__unsupported.json`](device-packs/__unsupported.json)。

判定依据包括：官方驱动界面本身就没有电量显示、遍历全部可读命令均无电量字段、
候选字段在插拔充电线时纹丝不动。程序遇到这些 VID:PID 会直接标注"不提供电量"，
不会反复重试或报错。

> ⚠️ 只支持 **2.4G 无线模式**。这几台设备切到有线模式后厂商接口会消失（线只用来充电）。

---

## 快速开始

需要 Windows 10/11 + Python 3.8+。

```bash
python -m pip install pystray pillow hidapi
python src/prototype/app.py
```

| 入口 | 用途 |
|---|---|
| `python src/prototype/app.py` | 正常运行（托盘 + 任务栏） |
| `run_debug.bat` | 带控制台启动，出错能看见 |
| `run_silent.vbs` | 无窗口启动 |
| `python src/prototype/app.py --once` | 只读一次，打印结果 |
| `python src/prototype/app.py --selftest` | 显示叠加层并截图，验证渲染 |
| `python tools/probe/read_packs.py` | 命令行读取全部设备 |

日志：`%LOCALAPPDATA%\BatteryDisplay\app.log`
后台程序没有控制台，这是唯一的排错手段（记录启动、每次刷新、崩溃堆栈）。

### 开机自启

已写入 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，无需管理员权限、不触发 UAC。

关闭方式：任务管理器 → 启动应用 → 禁用 `BatteryDisplay`，或

```powershell
Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name BatteryDisplay
```

程序带**单实例保护**：自启后再手动双击不会跑出第二份。

---

## 工作原理

```
device-packs/*.json  →  engine.py        发帧 / 收帧 / 校验 / 解析
                             ↓
                        app.py            60 秒轮询 + 1.5 秒轻检查
                             ├──→ overlay.py   任务栏文字（layered 窗口）
                             └──→ 托盘图标 + 右键菜单
```

几个关键实现细节，都是踩出来的，不是查文档能查到的：

**任务栏叠加用顶层 layered 窗口，不是 `SetParent` 子窗口。**
Win11 任务栏的内容由 XAML（Composition）渲染，塞进去的子窗口会被盖住。

**不画背景，只画带 alpha 的文字。**
很多人的任务栏是透明的（TranslucentTB 等），画一个底色块就把效果毁了。

**白字 + 1px 深描边，不做背景色采样。**
动态壁纸（Wallpaper Engine）的亮度一直在变，采样永远滞后；描边方案在明暗背景上都成立。

**右对齐钉在通知区左边缘，不用固定坐标。**
应用按钮的宽度随打开窗口数变化，锚它就会漂；通知区位置稳定得多。

**先放大 4 倍绘制再 LANCZOS 缩回。**
托盘和任务栏只有 16~20px 高，直接在小尺寸上画全是锯齿。

**设备休眠时不去唤醒它。**
读电量会唤醒无线射频，一个帮你省电的工具把电池耗光就荒唐了。读不到就显示上次读数并降透明度。

---

## 自己加一台设备

不需要改代码，写一个 JSON 就行。三步：

### 1. 认接口

设备管理器 → 找到接收器 → 属性 → 详细信息 → 硬件 ID：

```
HID\VID_373B&PID_1216&REV_0100&MI_02
     ^^^^      ^^^^              ^^
     VID       PID            接口序号
```

接收器通常是 **HID 复合设备**，有键盘 / 鼠标 / 多媒体 / 厂商自定义好几个接口，
**电量只藏在 usage page 为 `0xFF00~0xFFFF` 的那个厂商接口里**。

先看全部接口：

```bash
python tools/probe/probe_hid.py --vid 373B --pid 1216 --listen 8
```

### 2. 找命令

最省事的是先查社区有没有人做过（这类国产外设的方案商高度重合）。
找不到就抓包：装 Wireshark 时勾选 **USBPcap**，打开官方驱动，
录下它刷新电量时发出的帧。

判据：同一对帧周期性出现、长度固定、电量变化时只有 1~2 个字节跟着变。

### 3. 写设备包

照着 [`device-packs/atk-a9-plus.json`](device-packs/atk-a9-plus.json) 改：

```jsonc
{
  "key": "my-device",                    // 唯一标识
  "name": "显示名",                       // 出现在托盘菜单里
  "type": "keyboard",                    // keyboard / mouse，决定显示哪个图标
  "transport": {
    "type": "hid",
    "io": "output_input",                // Feature Report 走不通时用这个
    "report_bytes": 33                   // 报文总长度（含 Report ID）
  },
  "match": {                             // 怎么认出这台设备
    "vid": "0x373B",
    "pid": "0x1216",
    "interface": 2,
    "usage_page": "0xFF05"
  },
  "read": {
    "query": ["08 7D 72 02 00 01 07 01"],  // 要发的帧，首字节是 Report ID
    "checksum": { "mode": "none" },        // none / sum_all（末字节 = 前面所有字节之和）
    "attempts": 3,
    "poke_interval_ms": 120,               // 连发间隔，用于唤醒休眠设备
    "listen_ms": 2500,                     // 每次等回包多久
    "reply": {
      "contains": ["72 00 05"],            // 回包特征，防止把别的帧当数据
      "battery": { "byte": 7 },            // 电量在第几字节（从 0 数）
      "range": [1, 100]                    // 越界判为不可信
    }
  }
}
```

数值不只能表达百分比，还支持电压换算、档位映射、位域提取——字段全表见
[技术方案文档](docs/技术方案-无线外设电量任务栏显示.md) 的 §2.4。

写好后跑 `python tools/probe/read_packs.py` 验证，通过后主程序会自动加载。

---

## 项目结构

```
device-packs/      每台设备一份 JSON（协议描述）+ 不支持清单
src/prototype/     可运行程序
  engine.py          设备包引擎：发帧、校验、解析
  overlay.py         任务栏叠加层：窗口、图标绘制、定位、全屏检测
  app.py             主程序：调度、状态、托盘
tools/probe/       逆向与调试工具
  probe_hid.py       通用 HID 调试台（枚举 / 监听 / 发帧）
  inspect_taskbar.py 任务栏结构与透明实现探测
  find_brightness.py A/B/C 三次快照差分，定位配置字段
  overlay_demo.py    叠加层效果验证（贴图 + 截图）
docs/              技术方案与完整逆向记录
```

---

## 已知限制

- 只按**底部**任务栏定位；任务栏换到左 / 上 / 右边会错位
- 多显示器只支持主屏
- 设备休眠时显示上次读数（置灰），不主动唤醒
- 原型是 Python 实现，常驻内存偏大；正式版计划迁移到 C#/.NET

---

## 逆向记录

协议怎么一步步摸出来的、踩了哪些坑（把校验位当成电量、休眠设备不回包、
差分对比漏了分片导致结论反转、VBScript 的 CRLF 坑），都记在
[`docs/技术方案-无线外设电量任务栏显示.md`](docs/技术方案-无线外设电量任务栏显示.md)。

参考过的开源实现：

- [sonix-keyboard-studio](https://github.com/donglover69/sonix-keyboard-studio) —— SONiX 协议逆向（狼蛛 / AJAZZ 同族）
- [Wireless-Device-Battery-Monitor](https://github.com/krisgfx9/Wireless-Device-Battery-Monitor) —— 大量国产外设的电量协议清单
- [f75-linux-manager](https://github.com/harshvats17/f75-linux-manager) —— 同一接收器的 Linux 实现与结论
- [TrafficMonitor](https://github.com/zhongyang219/TrafficMonitor) —— Win11 任务栏叠加的可行做法
