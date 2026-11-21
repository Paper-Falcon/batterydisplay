"""BatteryDisplay 常驻程序：采集 + 任务栏文字 + 托盘图标。

一个进程同时管理两处显示：
  · 任务栏右侧（通知区左边）的图标 + 百分比
  · 系统托盘图标（显示最低电量，右键看全部）

用法:
    python src/prototype/app.py              # 正常运行
    python src/prototype/app.py --once       # 只读一次，打印结果
    python src/prototype/app.py --no-overlay # 只开托盘，不贴任务栏
    python src/prototype/app.py --selftest   # 显示叠加层 2 秒并截图，用于验证
"""

import os
import subprocess
import sys
import threading
import time
import traceback
import ctypes

import pystray
from PIL import Image, ImageDraw, ImageFont

import engine
import overlay
import settings

REFRESH_SECONDS = 60
TICK_SECONDS = 1.5

# 低电量提醒：电量越过阈值时弹一次通知。
# 回升到「阈值 + 余量」以上才重新武装，避免在边界反复弹窗。
ALERT_THRESHOLDS = (20, 10)
ALERT_REARM_MARGIN = 5

_ERROR_ALREADY_EXISTS = 183
_instance_mutex = None


def acquire_single_instance():
    """全局互斥，防止开机自启 + 手动双击跑出两个实例。

    两个实例会画两层叠加文字、出现两个托盘图标，很难看出是重复启动。
    返回 True 表示本进程是唯一实例。
    """
    global _instance_mutex
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _instance_mutex = kernel32.CreateMutexW(None, False, "Local\\BatteryDisplay_SingleInstance")
        return ctypes.get_last_error() != _ERROR_ALREADY_EXISTS
    except Exception:
        return True   # 互斥创建失败时不阻止启动，宁可多开也不要打不开


def _write_crash(text):
    """只在未捕获异常时留一份痕迹，不写常规运行日志。

    后台程序用 pythonw 启动时没有控制台，崩了会完全无声无息。
    """
    try:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        folder = os.path.join(base, "BatteryDisplay")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "crash.log"), "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}]\n{text}\n")
    except Exception:
        pass


def badge_image(level):
    """托盘小徽章：色块 + 白色数字。"""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if level is None:
        bg = (150, 150, 150, 255)
    elif level > 50:
        bg = (34, 197, 94, 255)
    elif level > 20:
        bg = (245, 158, 11, 255)
    else:
        bg = (239, 68, 68, 255)

    d.rounded_rectangle([1, 1, size - 2, size - 2], radius=14, fill=bg)
    text = "--" if level is None else str(level)
    font = None
    for name in ("arialbd.ttf", "arial.ttf", "segoeuib.ttf"):
        try:
            font = ImageFont.truetype(name, 40 if len(text) <= 2 else 32)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    d.text((size // 2, size // 2 + 1), text, font=font,
           fill=(255, 255, 255, 255), anchor="mm")
    return img


class App:
    def __init__(self, show_overlay=True):
        self.readable, self.unsupported = engine.load_targets()
        self.state = {
            d["key"]: {
                "name": d["name"],
                "kind": d.get("type", "other"),
                "level": None,
                "status": "idle",
                "detail": "等待读取",
                "ts": 0.0,
                "alerted": set(),      # 已经弹过提醒的阈值
            }
            for d in self.readable
        }
        self.overlay = overlay.Overlay()
        self.cfg = settings.load()
        # 命令行 --no-overlay 是硬开关，配置里的开关不能把它顶掉
        self.overlay_cli_disabled = not show_overlay
        self.show_overlay = self.cfg["overlay_enabled"] and not self.overlay_cli_disabled
        self.cfg_mtime = self._cfg_mtime()
        self.cached_img = None
        self.cached_pos = None
        self.stop = False
        self.icon = None
        self.lock = threading.Lock()

    # ── 采集 ──
    def refresh(self):
        for entry in self.readable:
            res = engine.read_device(entry)
            with self.lock:
                st = self.state[entry["key"]]
                if res["status"] == "ok":
                    st["level"] = res["level"]
                    st["status"] = "ok"
                    st["detail"] = res["detail"]
                    st["ts"] = time.time()
                else:
                    st["status"] = res["status"]
                    st["detail"] = res["detail"]
                self.check_alert(st)
            # 逐台刷新显示：先读到的先出现，不必等全部读完
            self.render()
        self.update_tray()

    # ── 低电量提醒 ──
    def check_alert(self, st):
        """越过阈值弹一次；一次掉穿多个阈值也只弹一条。"""
        level = st["level"]
        if level is None or st["status"] != "ok" or not self.cfg["alerts_enabled"]:
            return

        newly = [th for th in self.cfg["alert_thresholds"]
                 if level <= th and th not in st["alerted"]]
        if newly:
            st["alerted"].update(newly)
            self.notify_low(st["name"], level)

        # 充电回升后重新武装，留 5% 余量防止在阈值上反复弹
        for th in list(st["alerted"]):
            if level > th + ALERT_REARM_MARGIN:
                st["alerted"].discard(th)

    def notify_low(self, name, level):
        if not self.icon:
            return
        try:
            self.icon.notify(f"{name} 电量仅剩 {level}%，该充电了", "电量偏低")
        except Exception:
            pass

    def strip_items(self):
        items = []
        for st in self.state.values():
            # 接收器已经拔掉了，就不要在任务栏留残影
            if st["status"] == "offline":
                continue
            kind = st["kind"] if st["kind"] in ("keyboard", "mouse") else "other"
            stale = st["status"] != "ok" and st["level"] is not None
            items.append((kind, st["level"], stale))
        return items

    # ── 配置 ──
    def _cfg_mtime(self):
        try:
            return os.path.getmtime(settings.path())
        except OSError:
            return 0

    def reload_settings_if_changed(self):
        """控制面板是独立进程，靠文件 mtime 感知变更。"""
        mtime = self._cfg_mtime()
        if mtime == self.cfg_mtime:
            return
        self.cfg_mtime = mtime
        self.cfg = settings.load()
        self.show_overlay = self.cfg["overlay_enabled"] and not self.overlay_cli_disabled
        self.cached_pos = None      # 字号可能变了，强制重画
        self.render()
        self.update_tray()

    def open_settings(self, *_):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "settings_window.py")
        try:
            subprocess.Popen([sys.executable, script])
        except Exception:
            pass

    # ── 显示 ──
    def render(self):
        metrics = overlay.taskbar_metrics()
        if not metrics:
            return
        if overlay.is_fullscreen() or metrics["hidden"] or not self.show_overlay:
            self.overlay.hide()
            return

        items = self.strip_items()
        if not items:
            # 一台在线的设备都没有，整条都别显示
            self.overlay.hide()
            self.cached_img = None
            self.cached_pos = None
            return

        img = overlay.render_strip(items, font_size=self.cfg["font_size"],
                                  dpi_scale=metrics["dpi_scale"])
        self.cached_img = img
        self.place(img, metrics)

    def place(self, img, metrics):
        tray_left = metrics["tray_left"] or (metrics["left"] + metrics["width"])
        gap = int(16 * metrics["dpi_scale"])
        x = tray_left - img.size[0] - gap
        y = metrics["top"] + (metrics["height"] - img.size[1]) // 2

        min_x = metrics["apps_right"] + int(8 * metrics["dpi_scale"])
        if x < min_x:
            x = min_x

        if self.cached_pos == (x, y) and self.overlay.visible:
            return
        self.overlay.show(img, x, y)
        self.cached_pos = (x, y)

    def tick(self):
        """轻量轮询：只处理位置/可见性变化，不重新读设备。"""
        self.reload_settings_if_changed()
        metrics = overlay.taskbar_metrics()
        if not metrics:
            return
        if overlay.is_fullscreen() or metrics["hidden"] or not self.show_overlay:
            self.overlay.hide()
            self.cached_pos = None
            return
        if self.cached_img is not None:
            self.place(self.cached_img, metrics)

    # ── 托盘 ──
    def lowest_level(self):
        levels = [s["level"] for s in self.state.values()
                  if s["level"] is not None and s["status"] != "offline"]
        return min(levels) if levels else None

    def tooltip(self):
        lines = []
        for st in self.state.values():
            if st["level"] is None:
                lines.append(f"{st['name']}: {st['detail']}")
            elif st["status"] != "ok":
                age = int((time.time() - st["ts"]) / 60)
                lines.append(f"{st['name']}: {st['level']}%（{age} 分钟前）")
            else:
                lines.append(f"{st['name']}: {st['level']}%")
        if self.unsupported:
            lines.append(f"另有 {len(self.unsupported)} 台设备不提供电量")
        return "\n".join(lines)

    def build_menu(self):
        items = []
        for st in self.state.values():
            if st["level"] is None:
                label = f"{st['name']}  —  {st['detail']}"
            elif st["status"] != "ok":
                label = f"{st['name']}  —  {st['level']}%（上次读数）"
            else:
                label = f"{st['name']}  —  {st['level']}%"
            items.append(pystray.MenuItem(label, None, enabled=False))

        if self.unsupported:
            items.append(pystray.MenuItem(
                f"{len(self.unsupported)} 台设备不提供电量", None, enabled=False))

        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("控制面板…", self.open_settings))
        items.append(pystray.MenuItem(
            "隐藏任务栏文字" if self.show_overlay else "显示任务栏文字",
            self.toggle_overlay))
        items.append(pystray.MenuItem(
            "立即刷新", lambda: threading.Thread(target=self.refresh, daemon=True).start()))
        items.append(pystray.MenuItem("退出", self.quit))
        return pystray.Menu(*items)

    def update_tray(self):
        if not self.icon:
            return
        self.icon.icon = badge_image(self.lowest_level())
        self.icon.title = self.tooltip()
        self.icon.menu = self.build_menu()
        try:
            self.icon.update_menu()
        except Exception:
            pass

    def toggle_overlay(self, *_):
        self.show_overlay = not self.show_overlay
        if not self.show_overlay:
            self.overlay.hide()
            self.cached_pos = None
        else:
            self.render()
        self.update_tray()

    def quit(self, *_):
        self.stop = True
        if self.icon:
            self.icon.stop()

    # ── 主循环 ──
    def worker(self):
        next_refresh = 0.0
        while not self.stop:
            now = time.time()
            try:
                if now >= next_refresh:
                    self.refresh()
                    next_refresh = now + self.cfg["refresh_seconds"]
                else:
                    self.tick()
            except Exception as e:
                print(f"[循环异常] {e}")
            time.sleep(TICK_SECONDS)
        self.overlay.destroy()

    def run(self):
        # 先画一版占位（"--"），让用户双击后立刻看到东西，
        # 而不是等第一次读完设备（休眠设备要等好几秒）才出现。
        self.render()

        self.icon = pystray.Icon(
            "BatteryDisplay", icon=badge_image(None),
            title="BatteryDisplay 启动中…", menu=self.build_menu())
        threading.Thread(target=self.worker, daemon=True).start()
        self.icon.run()


def selftest(path="tmp/app_selftest.png"):
    """显示叠加层并截图，便于无人值守验证渲染是否正常。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)   # tmp/ 可能已被清理
    app = App()
    app.refresh()
    time.sleep(1.0)
    from PIL import ImageGrab
    metrics = overlay.taskbar_metrics()
    if metrics and app.cached_pos:
        x, y = app.cached_pos
        w, h = app.cached_img.size
        shot = ImageGrab.grab(bbox=(x - 12, y - 6, x + w + 12, y + h + 6))
        shot.save(path)
        print(f"已截图: {path} ({shot.size[0]}×{shot.size[1]})")
    else:
        print("没能放置叠加层")
    app.overlay.destroy()
    return app


def test_alert():
    """弹两条低电量通知，用于验证提醒效果（不需要真的等到掉电）。"""
    app = App()
    app.icon = pystray.Icon(
        "BatteryDisplay", badge_image(9), "BatteryDisplay 提醒测试",
        pystray.Menu(pystray.MenuItem("退出", lambda *_: app.quit())))
    try:
        app.icon.run_detached()
    except Exception:
        threading.Thread(target=app.icon.run, daemon=True).start()

    time.sleep(1.2)
    for name, lv in (("键盘", 18), ("鼠标", 8)):
        app.notify_low(name, lv)
        time.sleep(0.6)
    print("已发出 2 条测试通知：键盘 18%、鼠标 8%")
    time.sleep(4)
    app.quit()


def main():
    args = sys.argv[1:]

    try:
        if "--once" in args:
            readable, unsupported = engine.load_targets()
            for entry in readable:
                res = engine.read_device(entry)
                shown = f"{res['level']}%" if res["status"] == "ok" else res["detail"]
                print(f"{entry['name']:<20}{shown}")
            for entry in unsupported:
                print(f"{entry['name']:<20}此设备不提供电量（已实测）")
            return

        if "--selftest" in args:
            selftest()
            return

        if "--test-alert" in args:
            test_alert()
            return

        if not acquire_single_instance():
            return   # 已有实例在跑，静默退出

        App(show_overlay="--no-overlay" not in args).run()
    except Exception:
        _write_crash(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
