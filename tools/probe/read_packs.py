"""声明式设备包引擎（最小验证版）。

不包含任何设备专属代码：所有协议细节都来自 device-packs/*.json。
这是对「一个通用引擎 + N 份配置」架构的可行性验证。

用法:
    python read_packs.py
"""

import glob
import json
import os
import sys
import time

try:
    import hid
except ImportError:
    sys.exit("缺少依赖: python -m pip install hidapi")

PACK_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "device-packs")


def hexs(data):
    return " ".join(f"{b:02X}" for b in data)


def parse_hex(s):
    return bytes(int(x, 16) for x in s.split())


def find_device(match):
    vid = int(match["vid"], 16)
    pid = int(match["pid"], 16)
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

    dev_info = find_device(entry["match"])
    if not dev_info:
        return "offline", "设备不在线", None

    frame = build_frame(read_cfg, transport)
    attempts = read_cfg.get("attempts", 3)
    pause = read_cfg.get("poke_interval_ms", 120) / 1000.0
    listen = read_cfg.get("listen_ms", 800)
    contains = [parse_hex(c) for c in reply_cfg.get("contains", [])]

    try:
        dev = hid.device()
        dev.open_path(dev_info["path"])
    except Exception as e:
        return "error", f"打开失败: {e}", None

    try:
        for _ in range(attempts):
            try:
                dev.write(frame)
            except Exception as e:
                return "error", f"写入失败: {e}", None
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
                return "suspect", f"读数 {level} 超出可信范围 {lo}-{hi}", raw
            return "ok", level, raw
        return "silent", f"{attempts} 次均无有效回包（设备可能休眠，请按键唤醒）", None
    finally:
        dev.close()


def main():
    files = sorted(glob.glob(os.path.join(PACK_DIR, "*.json")))
    if not files:
        sys.exit(f"没有找到设备包: {PACK_DIR}")

    packs = []
    known_unsupported = []
    for path in files:
        with open(path, encoding="utf-8") as f:
            pack = json.load(f)
        if pack.get("format") == "batterydisplay-unsupported-list":
            known_unsupported.extend(pack.get("devices", []))
        else:
            packs.extend(pack.get("devices", []))

    print(f"加载 {len(packs)} 个可读设备，另有 {len(known_unsupported)} 个已知不支持\n")

    results = []
    for entry in packs:
        if not entry.get("read"):
            continue
        status, detail, raw = read_device(entry)
        results.append((entry["name"], status, detail, raw))
        print(f"· {entry['name']:<16} [{entry['key']}]")

    for entry in known_unsupported:
        results.append((entry["name"], "不支持", "此设备不提供电量（已实测）", None))

    print("\n" + "=" * 66)
    print(f"{'设备':<20}{'状态':<10}{'电量'}")
    print("-" * 66)
    for name, status, detail, raw in results:
        if status == "ok":
            print(f"{name:<20}{'读取成功':<10}{detail}%")
        else:
            print(f"{name:<20}{status:<10}{detail}")
    print("=" * 66)

    ok = sum(1 for _, s, _, _ in results if s == "ok")
    print(f"\n成功 {ok}/{len(results)}")


if __name__ == "__main__":
    main()
