"""任务栏文字叠加层。

要点（都是实测踩出来的）：
  · 用顶层 layered 窗口，不用 SetParent 子窗口 —— Win11 任务栏由 XAML 渲染，子窗口会被盖住
  · 不画背景，只画带 alpha 的文字/图标 —— 用户任务栏是透明的（TranslucentTB），画底色就毁了
  · 白字 + 深色描边 —— 动态壁纸亮度一直在变，不能靠采样壁纸选颜色
  · 右对齐钉在通知区左边缘 —— 应用按钮宽度会变，锚它就会漂
  · 先放大 4 倍绘制再缩回 —— 小尺寸下才有抗锯齿
"""

import ctypes
import ctypes.wintypes as wt

from PIL import Image, ImageDraw, ImageFont

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

SCALE = 4
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
SW_SHOWNOACTIVATE = 4
SW_HIDE = 0
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01

FG = (255, 255, 255, 255)
HALO = (0, 0, 0, 220)
WARN = (255, 196, 84, 255)     # 越过较高阈值：琥珀色
DANGER = (255, 96, 96, 255)    # 越过最低阈值：红色


def _level_color(level, thresholds):
    """电量正常时用白色，低了才变色 —— 颜色要稀缺才有信号意义。"""
    if level is None or not thresholds:
        return FG
    ths = sorted(thresholds, reverse=True)
    if level <= ths[-1]:
        return DANGER
    if level <= ths[0]:
        return WARN
    return FG


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


# ─────────────────────────── 图标绘制 ───────────────────────────

def draw_icon(d, kind, x, y, s, fg=FG, halo=HALO):
    """实心造型 + 深色外框。16px 下细线条会糊成一片，实心块对比更强。"""
    inset = 1.5 * s

    if kind == "keyboard":
        w, h = 18 * s, 12 * s
        radius = 2.2 * s
        d.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=halo)
        d.rounded_rectangle([x + inset, y + inset, x + w - inset, y + h - inset],
                            radius=max(1, radius - inset), fill=fg)
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
        mid = x + w / 2
        d.line([mid, y + 2.6 * s, mid, y + h * 0.44], fill=halo, width=int(1.7 * s))

    else:  # 兜底：圆点
        w = h = 12 * s
        d.ellipse([x, y, x + w, y + h], fill=halo)
        d.ellipse([x + inset, y + inset, x + w - inset, y + h - inset], fill=fg)


ICON_W = {"keyboard": 18, "mouse": 11, "other": 12}
ICON_H = {"keyboard": 12, "mouse": 16, "other": 12}


def _alpha(c, factor):
    return (c[0], c[1], c[2], int(c[3] * factor))


def render_strip(items, font_size=15, dpi_scale=1.0, thresholds=(20, 10)):
    """items = [(kind, level)] 或 [(kind, level, dim)] → 透明条 RGBA。

    dim=True 的那一项会整体降透明度 —— 表示"这是上次读数，现在读不到"。
    level 低于 thresholds 时文字与图标变琥珀/红色，余光就能察觉。
    dpi_scale=1.25 时输出尺寸放大 25%，保证高 DPI 下不模糊。
    """
    u = SCALE * dpi_scale          # 长度单位
    fs = font_size * u             # 字号

    items = [(it[0], it[1], it[2] if len(it) > 2 else False) for it in items]

    font = None
    for name in ("msyhbd.ttc", "msyh.ttc", "segoeuib.ttf", "arialbd.ttf"):
        try:
            font = ImageFont.truetype(name, int(fs))
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    gap_icon_text = 4 * u
    gap_group = 14 * u
    pad_x, pad_y = 8 * u, 5 * u

    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    total_w = pad_x * 2
    for i, (kind, level, _dim) in enumerate(items):
        text = "--" if level is None else f"{level}%"
        tb = probe.textbbox((0, 0), text, font=font, stroke_width=int(u))
        total_w += ICON_W[kind] * u + gap_icon_text + (tb[2] - tb[0])
        if i < len(items) - 1:
            total_w += gap_group

    content_h = max(max(ICON_H[k[0]] for k in items) * u, fs)
    total_h = content_h + pad_y * 2

    img = Image.new("RGBA", (int(total_w), int(total_h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    cx = pad_x
    for i, (kind, level, dim) in enumerate(items):
        f = 0.45 if dim else 1.0
        color = _level_color(level, thresholds)
        fg, halo = _alpha(color, f), _alpha(HALO, f)

        iy = (total_h - ICON_H[kind] * u) / 2
        draw_icon(d, kind, cx, iy, u, fg=fg, halo=halo)
        cx += ICON_W[kind] * u + gap_icon_text

        text = "--" if level is None else f"{level}%"
        tb = probe.textbbox((0, 0), text, font=font, stroke_width=int(u))
        ty = (total_h - (tb[3] - tb[1])) / 2 - tb[1]
        d.text((cx, ty), text, font=font, fill=fg,
               stroke_width=int(u), stroke_fill=halo)
        cx += (tb[2] - tb[0])
        if i < len(items) - 1:
            cx += gap_group

    return img.resize((max(1, int(total_w / SCALE)), max(1, int(total_h / SCALE))),
                      Image.LANCZOS)


# ─────────────────────────── 窗口 ───────────────────────────

class Overlay:
    def __init__(self):
        self.hwnd = None
        self.visible = False

    def ensure_window(self):
        if self.hwnd:
            return self.hwnd
        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST,
            "STATIC", "BatteryDisplayOverlay", WS_POPUP, 0, 0, 1, 1,
            None, None, None, None)
        if not self.hwnd:
            raise OSError(f"建窗失败: {ctypes.get_last_error()}")
        return self.hwnd

    def show(self, img, x, y):
        hwnd = self.ensure_window()
        w, h = img.size

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
        try:
            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = w
            bmi.bmiHeader.biHeight = -h
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
            dst = wt.POINT(int(x), int(y))
            blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)

            ok = user32.UpdateLayeredWindow(
                hwnd, screen, ctypes.byref(dst), ctypes.byref(size),
                mem, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)
            if not ok:
                raise OSError(f"UpdateLayeredWindow 失败: {ctypes.get_last_error()}")

            gdi32.DeleteObject(ctypes.c_void_p(hbmp))
        finally:
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(None, screen)

        if not self.visible:
            user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            self.visible = True

    def hide(self):
        if self.hwnd and self.visible:
            user32.ShowWindow(self.hwnd, SW_HIDE)
            self.visible = False

    def destroy(self):
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
            self.visible = False


# ─────────────────────────── 环境探测 ───────────────────────────

def taskbar_metrics():
    """返回任务栏几何信息；找不到则 None。"""
    hwnd = user32.FindWindowW("Shell_TrayWnd", None)
    if not hwnd:
        return None

    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    metrics = {
        "hwnd": hwnd,
        "left": r.left, "top": r.top,
        "width": r.right - r.left, "height": r.bottom - r.top,
        "visible": bool(user32.IsWindowVisible(hwnd)),
        "tray_left": None,
        "apps_right": 0,
        "dpi_scale": 1.0,
    }

    try:
        metrics["dpi_scale"] = user32.GetDpiForWindow(hwnd) / 96.0
    except Exception:
        pass

    # 自动隐藏的任务栏会被推到屏幕外，但仍算"可见"。
    # 这种情况要当成隐藏处理，否则叠加层会挂在屏幕底边露出一条。
    screen_h = user32.GetSystemMetrics(1)
    metrics["hidden"] = (not metrics["visible"]) or metrics["top"] >= screen_h - 4

    children = []
    CB = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _):
        children.append(h)
        return True

    user32.EnumChildWindows(hwnd, CB(cb), 0)
    for child in children:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(child, buf, 256)
        cr = wt.RECT()
        user32.GetWindowRect(child, ctypes.byref(cr))
        if buf.value in ("ReBarWindow32", "MSTaskSwWClass"):
            metrics["apps_right"] = max(metrics["apps_right"], cr.right)
        elif buf.value == "TrayNotifyWnd":
            metrics["tray_left"] = cr.left
    return metrics


def is_fullscreen():
    """全屏应用/演示模式时返回 True，此时应隐藏叠加层。"""
    try:
        shell32.SHQueryUserNotificationState.argtypes = [ctypes.POINTER(ctypes.c_int)]
        state = ctypes.c_int()
        shell32.SHQueryUserNotificationState(ctypes.byref(state))
        return state.value in (2, 3, 4)   # BUSY / D3D_FULLSCREEN / PRESENTATION
    except Exception:
        return False
