"""探测任务栏结构 + 透明实现方式 + 文字对比度取色依据。

P2 要在任务栏上叠文字，必须先搞清楚三件事：
  1. Shell_TrayWnd 还在不在（有没有被第三方工具替换）
  2. 透明是系统「透明效果」还是 TranslucentTB 这类工具做的
  3. 任务栏背后那块壁纸有多亮 —— 决定文字该用黑还是白

用法:
    python inspect_taskbar.py
"""

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_EX_LAYERED = 0x00080000
SPI_GETDESKWALLPAPER = 0x0073

TOOLS = ["TranslucentTB", "StartAllBack", "ExplorerPatcher", "ep_setup",
         "TrayS", "taskbarx", "TaskbarX", "RoundedTB", "Windhawk"]


def class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_text(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def rect(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def enum_children(hwnd):
    out = []
    CB = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(child, _):
        out.append(child)
        return True

    user32.EnumChildWindows(hwnd, CB(cb), 0)
    return out


def main():
    print("=" * 70)
    print("任务栏结构")
    print("=" * 70)

    tray = user32.FindWindowW("Shell_TrayWnd", None)
    if not tray:
        print("✗ 找不到 Shell_TrayWnd —— 任务栏可能被第三方软件完全替换")
        return

    x, y, w, h = rect(tray)
    style = user32.GetWindowLongW(tray, GWL_STYLE)
    exstyle = user32.GetWindowLongW(tray, GWL_EXSTYLE)
    print(f"Shell_TrayWnd   句柄=0x{tray:X}  类名={class_name(tray)}")
    print(f"  位置=({x},{y})  尺寸={w}×{h}")
    print(f"  可见={bool(user32.IsWindowVisible(tray))}  "
          f"WS_EX_LAYERED={bool(exstyle & WS_EX_LAYERED)}")

    try:
        dpi = user32.GetDpiForWindow(tray)
        print(f"  DPI={dpi}  缩放={dpi / 96 * 100:.0f}%")
    except Exception:
        pass

    print("\n子窗口（找 TrayNotifyWnd 用来定位）:")
    kids = enum_children(tray)
    wanted = {"TrayNotifyWnd", "ReBarWindow32", "MSTaskSwWClass", "Start",
              "Windows.UI.Composition.DesktopWindowContentBridge"}
    for child in kids:
        cn = class_name(child)
        if cn in wanted or "Tray" in cn or "Notify" in cn:
            cx, cy, cw, ch = rect(child)
            vis = "可见" if user32.IsWindowVisible(child) else "隐藏"
            print(f"  {cn:<45} ({cx},{cy}) {cw}×{ch}  {vis}")

    print("\n" + "=" * 70)
    print("透明是怎么实现的")
    print("=" * 70)

    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        val, _ = winreg.QueryValueEx(key, "EnableTransparency")
        print(f"系统「透明效果」(EnableTransparency) = {val}"
              f"  {'→ 已开启' if val else '→ 已关闭'}")
    except Exception as e:
        print(f"读透明设置失败: {e}")

    print("\n检测第三方任务栏工具:")
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                             capture_output=True, text=True, timeout=10).stdout.lower()
        found = False
        for t in TOOLS:
            if t.lower() in out:
                print(f"  ✓ 发现 {t} 进程在运行")
                found = True
        if not found:
            print("  未发现已知任务栏美化工具在运行")
    except Exception as e:
        print(f"  进程检查失败: {e}")

    print("\n" + "=" * 70)
    print("任务栏背后的壁纸亮度（决定文字用黑还是白）")
    print("=" * 70)

    buf = ctypes.create_unicode_buffer(520)
    if not user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, 520, buf, 0):
        print("取壁纸路径失败")
        return
    wallpaper = buf.value
    print(f"壁纸: {wallpaper or '(空，可能是纯色或幻灯片)'}")

    if not wallpaper or not os.path.exists(wallpaper):
        print("无法读取壁纸文件，改用保守方案：白色文字 + 深色描边")
        return

    try:
        from PIL import Image
        img = Image.open(wallpaper).convert("RGB")
        iw, ih = img.size
        full_w = user32.GetSystemMetrics(0)
        full_h = user32.GetSystemMetrics(1)
        # 任务栏在屏幕底部，取壁纸对应区域的最后 8%
        top = int(ih * (y / full_h)) if full_h else int(ih * 0.92)
        strip = img.crop((0, max(0, top), iw, ih)).resize((32, 8))
        px = list(strip.getdata())
        lum = sum(0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in px) / len(px)
        print(f"壁纸尺寸 {iw}×{ih}，任务栏区域平均亮度 = {lum:.0f}/255")
        if lum > 160:
            print("→ 背景偏亮，建议用深色文字")
        elif lum < 90:
            print("→ 背景偏暗，建议用浅色文字")
        else:
            print("→ 中等亮度，建议加描边或半透明底衬")
    except Exception as e:
        print(f"壁纸分析失败: {e}")


if __name__ == "__main__":
    main()
