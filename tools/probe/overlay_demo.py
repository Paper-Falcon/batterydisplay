"""在透明任务栏上叠加文字的可行性 demo。

用顶层 layered 窗口（无背景、逐像素 alpha）画文字，贴在任务栏上方，
然后截取该区域存成 PNG —— 这样能真看到"透明任务栏 + 文字"的实际效果。

为什么用顶层窗口而不是 SetParent 子窗口：
Win11 任务栏内容由 XAML（Composition）渲染，加进去的子窗口可能被盖住。
顶层窗口 + WS_EX_TOOLWINDOW/NOACTIVATE 不抢焦点、不进 Alt+Tab，更稳。

用法:
    python overlay_demo.py                 # 默认位置，存 tmp/overlay_preview.png
    python overlay_demo.py --text "键盘 84%  鼠标 58%"
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import time

from PIL import Image, ImageDraw, ImageFont

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
SW_SHOWNOACTIVATE = 4
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]


def taskbar_rect():
    hwnd = user32.FindWindowW("Shell_TrayWnd", None)
    if not hwnd:
        raise SystemExit("找不到任务栏")
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def find_anchors():
    """返回 (应用按钮右边缘, 通知区左边缘)。

    定位要钉在「通知区左边缘」而不是应用按钮右侧：
    应用按钮宽度随打开的窗口数变化，位置会漂；
    通知区在 Win11 上位置稳定，右对齐才不会乱跳。
    """
    tray = user32.FindWindowW("Shell_TrayWnd", None)
    children = []
    CB = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, buf, 256)
        children.append((buf.value, h))
        return True

    user32.EnumChildWindows(tray, CB(cb), 0)

    apps_right = 0
    tray_left = None
    for cls, h in children:
        r = wt.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        if cls in ("ReBarWindow32", "MSTaskSwWClass"):
            apps_right = max(apps_right, r.right)
        elif cls == "TrayNotifyWnd":
            tray_left = r.left
    return apps_right, tray_left


SCALE = 4  # 先放大 4 倍绘制再缩回，得到抗锯齿边缘


def draw_icon(d, kind, x, y, s, fg=(255, 255, 255, 255), halo=(0, 0, 0, 220)):
    """实心造型 + 深色外框。

    16px 高度下，细线条会糊成一团；实心块 + 深边对比更强，更容易认出来。
    做法：先画深色实心块，再画内缩的白色实心块，留出深色边框。
    """
    inset = 1.5 * s

    if kind == "keyboard":
        w, h = 18 * s, 12 * s
        radius = 2.2 * s
        d.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=halo)
        d.rounded_rectangle([x + inset, y + inset, x + w - inset, y + h - inset],
                            radius=max(1, radius - inset), fill=fg)
        # 16px 下点阵会糊成黑块，改成 3 根竖键 + 一条空格键
        tick_w = 1.5 * s
        for fx in (0.26, 0.5, 0.74):
            dx = x + w * fx - tick_w / 2
            d.rectangle([dx, y + h * 0.28, dx + tick_w, y + h * 0.28 + 2.8 * s], fill=halo)
        d.rectangle([x + w * 0.2, y + h * 0.63, x + w * 0.8, y + h * 0.63 + 1.5 * s], fill=halo)
    elif kind == "mouse":
        w, h = 11 * s, 16 * s
        radius = w / 2
        d.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=halo)
        d.rounded_rectangle([x + inset, y + inset, x + w - inset, y + h - inset],
                            radius=max(1, radius - inset), fill=fg)
        # 只保留顶部竖线（左右键分界），不要横线，否则会变成十字
        mid = x + w / 2
        d.line([mid, y + 2.6 * s, mid, y + h * 0.44], fill=halo, width=int(1.7 * s))


def render_strip(items, font_size=15):
    """items = [(icon_kind, level), ...] → 带图标的透明条。"""
    s = SCALE
    fs = font_size * s
    font = None
    for name in ("msyhbd.ttc", "msyh.ttc", "segoeuib.ttf", "arialbd.ttf"):
        try:
            font = ImageFont.truetype(name, fs)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    icon_w = {"keyboard": 16 * s, "mouse": 10 * s}
    icon_h = {"keyboard": 11 * s, "mouse": 16 * s}
    gap_icon_text = 4 * s
    gap_group = 14 * s
    pad_x, pad_y = 8 * s, 5 * s

    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    total_w = pad_x * 2
    for i, (kind, level) in enumerate(items):
        text = "--" if level is None else f"{level}%"
        tb = probe.textbbox((0, 0), text, font=font, stroke_width=s)
        total_w += icon_w[kind] + gap_icon_text + (tb[2] - tb[0])
        if i < len(items) - 1:
            total_w += gap_group

    content_h = max(max(icon_h[k] for k, _ in items), fs)
    total_h = content_h + pad_y * 2

    img = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    cx = pad_x
    for i, (kind, level) in enumerate(items):
        iy = (total_h - icon_h[kind]) // 2
        draw_icon(d, kind, cx, iy, s)
        cx += icon_w[kind] + gap_icon_text

        text = "--" if level is None else f"{level}%"
        tb = probe.textbbox((0, 0), text, font=font, stroke_width=s)
        ty = (total_h - (tb[3] - tb[1])) // 2 - tb[1]
        d.text((cx, ty), text, font=font, fill=(255, 255, 255, 255),
               stroke_width=s, stroke_fill=(0, 0, 0, 215))
        cx += (tb[2] - tb[0])
        if i < len(items) - 1:
            cx += gap_group

    return img.resize((total_w // s, total_h // s), Image.LANCZOS)


def render_text(text, font_size=15, pad=(8, 5)):
    """白字 + 深色描边 —— 兼容旧调用。"""
    font = None
    for name in ("msyhbd.ttc", "msyh.ttc", "segoeuib.ttf", "arialbd.ttf"):
        try:
            font = ImageFont.truetype(name, font_size)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    probe = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(probe)
    box = d.textbbox((0, 0), text, font=font, stroke_width=1)
    w = box[2] - box[0] + pad[0] * 2
    h = box[3] - box[1] + pad[1] * 2

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((pad[0] - box[0], pad[1] - box[1]), text, font=font,
           fill=(255, 255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0, 210))
    return img


def show_layered(img, x, y):
    """把 PIL 图像贴成 layered 窗口，返回 hwnd。"""
    w, h = img.size
    hwnd = user32.CreateWindowExW(
        WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST,
        "STATIC", "BatteryOverlay", WS_POPUP, x, y, w, h, None, None, None, None)
    if not hwnd:
        raise SystemExit(f"建窗失败: {ctypes.get_last_error()}")

    # PIL 是 RGBA，UpdateLayeredWindow 要 BGRA 且预乘 alpha
    r, g, b, a = img.split()
    bgra = Image.merge("RGBA", (b, g, r, a))
    raw = bgra.tobytes()
    premul = bytearray(len(raw))
    for i in range(0, len(raw), 4):
        alpha = raw[i + 3]
        premul[i] = raw[i] * alpha // 255
        premul[i + 1] = raw[i + 1] * alpha // 255
        premul[i + 2] = raw[i + 2] * alpha // 255
        premul[i + 3] = alpha

    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h          # 负数 = 自上而下
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0

    bits = ctypes.c_void_p()
    gdi32.CreateDIBSection.restype = ctypes.c_void_p
    hbmp = gdi32.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    ctypes.memmove(bits, bytes(premul), len(premul))
    gdi32.SelectObject(mem, ctypes.c_void_p(hbmp))

    size = wt.SIZE(w, h)
    src = wt.POINT(0, 0)
    dst = wt.POINT(x, y)
    blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)

    ok = user32.UpdateLayeredWindow(
        hwnd, screen, ctypes.byref(dst), ctypes.byref(size),
        mem, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)
    if not ok:
        raise SystemExit(f"UpdateLayeredWindow 失败: {ctypes.get_last_error()}")

    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)

    gdi32.DeleteObject(ctypes.c_void_p(hbmp))
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, screen)
    return hwnd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default=None, help="改用纯文字模式时传这个")
    ap.add_argument("--keyboard", type=int, default=84)
    ap.add_argument("--mouse", type=int, default=58)
    ap.add_argument("--out", default="tmp/overlay_preview.png")
    ap.add_argument("--keep", type=float, default=1.2, help="显示多少秒后截图")
    args = ap.parse_args()

    tx, ty, tw, th = taskbar_rect()
    print(f"任务栏: ({tx},{ty}) {tw}×{th}")

    gap_x, tray_left = find_anchors()
    if args.text:
        img = render_text(args.text)
    else:
        img = render_strip([("keyboard", args.keyboard), ("mouse", args.mouse)])
    w = img.size[0]
    # 右对齐钉在通知区左侧，留 16px 空隙；并确保不压到应用按钮
    if tray_left:
        x = tray_left - w - 16
        if gap_x and x < gap_x + 8:
            x = tray_left - w - 16   # 空间不足时仍然优先贴通知区，宁可更靠右
    else:
        x = tx + tw // 2 - w // 2
    y = ty + (th - img.size[1]) // 2

    print(f"文字块 {w}×{img.size[1]}，放置于 ({x},{y})")
    print(f"锚点: 应用按钮右边缘={gap_x}  通知区左边缘={tray_left}  可用空间={tray_left - gap_x}px")

    hwnd = show_layered(img, x, y)
    time.sleep(args.keep)

    from PIL import ImageGrab
    pad = 12
    box = (x - pad, y - 6, x + img.size[0] + pad, y + img.size[1] + 6)
    shot = ImageGrab.grab(bbox=box)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    shot.save(args.out)
    print(f"已截图: {args.out}  ({shot.size[0]}×{shot.size[1]})")

    user32.DestroyWindow(hwnd)


if __name__ == "__main__":
    main()
