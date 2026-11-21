"""BatteryDisplay 托盘原型（Python）。

读 device-packs/*.json，轮询电量，在系统托盘显示最低电量，
右键菜单列出全部设备。这是 C# 正式版之前的可运行原型。

用法:
    python -m pip install pystray pillow hidapi
    python src/prototype/tray_app.py
    python src/prototype/tray_app.py --once     # 只打印一次，不开托盘
"""

import glob
import json
import os
import sys
import threading
import time

import hid
from PIL import Image, ImageDraw, ImageFont

PACK_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "device-packs")
REFRESH_SECONDS = 60


# ─────────────────────────── 设备包读取引擎 ───────────────────────────

def hexs(data):
    return " ".join(f"{b:02X}" for b in data)


def parse_hex(s):
    return bytes(int(x, 16) for x in s.split())


def find_device(match):
    vid, pid = int(match["vid"], 16), int(match["pid"], 16)
    for d in hid.enumerate():
        if d["vendor_id"] != vid or d["product_id"] != pid:
            continue
        if "interface" in match and d.get("interface_number") != match["interface"]:
            continue
        if "usage_page" in match and d.get("usage_page") != int(match["usage_page"], 16):
            continue
        if "usage" in match and d.get("usage") != int(match["usage"], 16):
            continue
        return d
    return None


def build_frame(read_cfg, transport):
    frame = bytearray(parse_hex(read_cfg["query"][0]))
    size = transport.get("report_bytes") or read_cfg.get("pad_to")
    if size and len(frame) < size:
        frame.extend(b"\x00" * (size - len(frame)))
    ck = read_cfg.get("checksum", {})
    if ck.get("mode") == "sum_all":
        lo, hi = ck.get("range", [0, len(frame) - 2])
        frame[ck["at"]] = sum(frame[lo:hi + 1]) & 0xFF
    return bytes(frame)


def read_device(entry):
    read_cfg = entry["read"]
    transport = entry.get("transport", {})
    reply_cfg = read_cfg["reply"]

    info = find_device(entry["match"])
    if not info:
        return {"status": "offline", "level": None, "detail": "未连接"}

    frame = build_frame(read_cfg, transport)
    attempts = read_cfg.get("attempts", 3)
    pause = read_cfg.get("poke_interval_ms", 120) / 1000.0
    listen = read_cfg.get("listen_ms", 800)
    contains = [parse_hex(c) for c in reply_cfg.get("contains", [])]

    try:
        dev = hid.device()
        dev.open_path(info["path"])
    except Exception as e:
        return {"status": "error", "level": None, "detail": f"打开失败: {e}"}

    try:
        for _ in range(attempts):
            try:
                dev.write(frame)
            except Exception as e:
                return {"status": "error", "level": None, "detail": f"写入失败: {e}"}
            time.sleep(pause)
            resp = dev.read(64, timeout_ms=listen)
            if not resp:
                continue
            raw = bytes(resp)
            if contains and not any(c in raw for c in contains):
                continue
            idx = reply_cfg["battery"]["byte"]
            if len(raw) <= idx:
                continue
            level = raw[idx]
            lo, hi = reply_cfg.get("range", [0, 100])
            if not (lo <= level <= hi):
                return {"status": "suspect", "level": None, "detail": f"读数 {level} 越界"}
            return {"status": "ok", "level": level, "detail": hexs(raw[:6])}
        return {"status": "silent", "level": None, "detail": "无回包（可能休眠）"}
    finally:
        dev.close()


def load_targets():
    """返回 (可读设备列表, 已知不支持列表)。"""
    readable, unsupported = [], []
    for path in sorted(glob.glob(os.path.join(PACK_DIR, "*.json"))):
        with open(path, encoding="utf-8") as f:
            pack = json.load(f)
        if pack.get("format") == "batterydisplay-unsupported-list":
            unsupported.extend(pack.get("devices", []))
        else:
            readable.extend(d for d in pack.get("devices", []) if d.get("read"))
    return readable, unsupported


# ─────────────────────────── 图标绘制 ───────────────────────────

def level_color(level):
    if level is None:
        return (150, 150, 150, 255)
    if level > 50:
        return (34, 197, 94, 255)
    if level > 20:
        return (245, 158, 11, 255)
    return (239, 68, 68, 255)


def make_icon(level):
    """把电量数字画成托盘图标。

    托盘实际只有 16px，透明底 + 细笔画的数字会糊成一团。
    改用「实色块 + 白字」：对比度高，缩到 16px 仍可辨认。
    """
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    bg = level_color(level)
    text = "--" if level is None else str(level)

    draw.rounded_rectangle([1, 1, size - 2, size - 2], radius=14, fill=bg)

    font = None
    for name in ("arialbd.ttf", "arial.ttf", "segoeuib.ttf"):
        try:
            font = ImageFont.truetype(name, 40 if len(text) <= 2 else 32)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    draw.text((size // 2, size // 2 + 1), text, font=font, fill=(255, 255, 255, 255), anchor="mm")
    return img


# ─────────────────────────── 托盘应用 ───────────────────────────

class TrayApp:
    def __init__(self):
        self.readable, self.unsupported = load_targets()
        self.state = {d["key"]: {"name": d["name"], "level": None, "status": "idle", "ts": 0}
                      for d in self.readable}
        self.lock = threading.Lock()
        self.icon = None
        self.stopping = False

    def refresh(self):
        for entry in self.readable:
            res = read_device(entry)
            with self.lock:
                cur = self.state[entry["key"]]
                if res["status"] == "ok":
                    cur["level"] = res["level"]
                    cur["status"] = "ok"
                    cur["ts"] = time.time()
                else:
                    # 读不到时保留上次值，但标记状态
                    cur["status"] = res["status"]
                    cur["detail"] = res["detail"]
        self.update_ui()

    def lowest_level(self):
        levels = [v["level"] for v in self.state.values() if v["level"] is not None]
        return min(levels) if levels else None

    def tooltip(self):
        lines = []
        for v in self.state.values():
            if v["level"] is None:
                lines.append(f"{v['name']}: {v.get('detail', '未知')}")
            else:
                age = int(time.time() - v["ts"])
                suffix = f"（{age // 60} 分钟前）" if v["status"] != "ok" else ""
                lines.append(f"{v['name']}: {v['level']}%{suffix}")
        if self.unsupported:
            lines.append(f"另有 {len(self.unsupported)} 台设备不提供电量")
        return "\n".join(lines)

    def build_menu(self):
        import pystray

        items = []
        for v in self.state.values():
            if v["level"] is None:
                label = f"{v['name']}  —  {v.get('detail', '未知')}"
            else:
                label = f"{v['name']}  —  {v['level']}%"
            items.append(pystray.MenuItem(label, None, enabled=False))
        if self.unsupported:
            items.append(pystray.MenuItem(
                f"{len(self.unsupported)} 台设备不提供电量", None, enabled=False))
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("立即刷新", lambda: threading.Thread(
            target=self.refresh, daemon=True).start()))
        items.append(pystray.MenuItem("退出", self.quit))
        return pystray.Menu(*items)

    def update_ui(self):
        if not self.icon:
            return
        self.icon.icon = make_icon(self.lowest_level())
        self.icon.title = self.tooltip()
        self.icon.menu = self.build_menu()
        self.icon.update_menu()

    def loop(self):
        while not self.stopping:
            try:
                self.refresh()
            except Exception as e:
                print(f"刷新异常: {e}")
            for _ in range(REFRESH_SECONDS):
                if self.stopping:
                    return
                time.sleep(1)

    def quit(self, *_):
        self.stopping = True
        if self.icon:
            self.icon.stop()

    def run(self):
        import pystray

        self.icon = pystray.Icon(
            "BatteryDisplay",
            icon=make_icon(None),
            title="正在读取…",
            menu=self.build_menu(),
        )
        threading.Thread(target=self.loop, daemon=True).start()
        self.icon.run()


def main():
    if "--once" in sys.argv:
        readable, unsupported = load_targets()
        for entry in readable:
            res = read_device(entry)
            shown = f"{res['level']}%" if res["status"] == "ok" else res["detail"]
            print(f"{entry['name']:<20}{shown}")
        for entry in unsupported:
            print(f"{entry['name']:<20}此设备不提供电量（已实测）")
        return

    TrayApp().run()


if __name__ == "__main__":
    main()
