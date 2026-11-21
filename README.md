# BatteryDisplay

无线键鼠的电量，直接显示在 Windows 任务栏上——**不用打开任何厂商驱动**。

## 为什么做这个

罗技要开 G HUB、雷蛇要开 Synapse、国产外设要开各自的驱动，才看得到电量。
而 Windows 本身没有一个统一的接口能读到无线外设的电量：BLE、经典蓝牙、
2.4G 私有接收器、有线 HID，四条路的取数方式完全不同。

这个项目的做法是：**把每个设备的协议写成一份 JSON，用一个通用引擎去执行**。
加新设备 = 加一个配置文件，不改代码。

## 当前状态

| 设备 | 连接 | 协议 | 状态 |
|---|---|---|---|
| ATK 蜻蜓 A9 Plus | 2.4G `373B:1216` | 厂商自定义 HID，`0xFF05` | ✅ 可用，已与官方网页驱动交叉验证 |
| 狼蛛 F108 Pro | 2.4G `05AC:024F` | SONiX，`0xFF60` / 33 字节帧 | ✅ 可用 |
| 重力星球键盘 | 2.4G `3554:FA09` | — | ❌ 该接收器不提供电量（有实测证据） |
| 杰理方案耳机 | 2.4G `4C4A:4155` | — | ❌ 无厂商通道，无法读取 |

不支持的两台设备不是"没做"，是**实测确认读不到**，证据记录在
`device-packs/__unsupported.json`，程序会直接标注而不是反复重试。

## 快速开始

```bash
python -m pip install pystray pillow hidapi
python src/prototype/app.py
```

其他入口：

| 命令 | 作用 |
|---|---|
| `run_debug.bat` | 带控制台启动，出错能看见 |
| `run_silent.vbs` | 无窗口启动，放进 `shell:startup` 可自启 |
| `python src/prototype/app.py --once` | 只读一次，打印结果 |
| `python src/prototype/app.py --selftest` | 显示叠加层并截图，用于验证渲染 |
| `python tools/probe/read_packs.py` | 命令行读取全部设备 |

日志位置：`%LOCALAPPDATA%\BatteryDisplay\app.log`（后台程序没有控制台，这是唯一排错手段）。

### 开机自启

已写入注册表：

```
HKCU\Software\Microsoft\Windows\CurrentVersion\Run
  BatteryDisplay = "<pythonw.exe 绝对路径>" "<app.py 绝对路径>"
```

- 不需要管理员权限，不触发 UAC
- 值里用的是**绝对路径**，不依赖启动目录（已验证从 `C:\Windows` 启动也能正常读设备）
- 程序带**单实例保护**：自启后再手动双击不会跑出第二份，日志会记一条"已有实例在运行"

关闭方式（任选）：

1. 任务管理器 → 启动应用 → 右键 `BatteryDisplay` → 禁用
2. 或者删除注册表值：
   `Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name BatteryDisplay`

## 工作原理

```
device-packs/*.json  →  engine.py  发帧/收帧/解析  →  app.py 调度
                                                      ├→ overlay.py  任务栏文字
                                                      └→ 托盘图标 + 菜单
```

几个关键实现细节，都是实测踩出来的：

- **任务栏叠加用顶层 layered 窗口**，不是 `SetParent` 子窗口——Win11 任务栏由 XAML 渲染，子窗口会被盖住
- **不画背景，只画带 alpha 的文字**——用户的任务栏是透明的（TranslucentTB），画底色就毁了效果
- **白字 + 1px 深描边**——动态壁纸亮度一直在变，采样配色会滞后
- **右对齐钉在通知区左边缘**——应用按钮宽度会变，锚它就会漂
- **先放大 4 倍绘制再缩回**——16px 高度下才不会有锯齿

## 目录结构

```
device-packs/      每个设备一份 JSON（协议描述）
src/prototype/     可运行原型：engine / overlay / app
tools/probe/       逆向与调试工具（协议探测、任务栏探测、差分对比）
docs/              技术方案与完整逆向记录
```

协议怎么摸出来的、踩了哪些坑，见 [`docs/技术方案-无线外设电量任务栏显示.md`](docs/技术方案-无线外设电量任务栏显示.md)。

## 已知限制

- 只按**底部**任务栏定位；任务栏换到左/上/右边会错位
- 多显示器只支持主屏
- 设备休眠时不唤醒它（读电量会耗电），显示上次读数并置灰
- 原型用 Python 实现，常驻内存偏大；正式版计划迁移到 C#/.NET
