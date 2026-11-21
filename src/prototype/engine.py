"""设备包读取引擎。

完全由 device-packs/*.json 驱动，不含任何设备专属代码。
加新设备 = 加一个 JSON 文件。
"""

import glob
import json
import os
import time

import hid

PACK_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "device-packs"))


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
    """返回 {status, level, detail}。status: ok / offline / silent / suspect / error。"""
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
        try:
            with open(path, encoding="utf-8") as f:
                pack = json.load(f)
        except Exception:
            continue
        if pack.get("format") == "batterydisplay-unsupported-list":
            unsupported.extend(pack.get("devices", []))
        else:
            readable.extend(d for d in pack.get("devices", []) if d.get("read"))
    # 键盘排在鼠标前面，与任务栏上的阅读习惯一致
    order = {"keyboard": 0, "mouse": 1}
    readable.sort(key=lambda d: order.get(d.get("type"), 9))
    return readable, unsupported
