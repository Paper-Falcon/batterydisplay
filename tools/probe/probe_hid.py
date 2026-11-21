"""通用 HID 调试台：枚举接口 / 被动监听 / 手动发帧。

被动监听是零写入的，优先用它找电量；只有监听不到时才考虑发帧。

用法:
    python probe_hid.py --vid 3554 --pid FA09 --listen 8
    python probe_hid.py --vid 3554 --pid FA09 --listen 8 --all-iface
    python probe_hid.py --vid 3554 --pid FA09 --send "08 7D 72 02 00 01 07 01"
"""

import argparse
import sys
import time

try:
    import hid
except ImportError:
    sys.exit("缺少依赖: python -m pip install hidapi")


def hexs(data):
    return " ".join(f"{b:02X}" for b in data)


def is_vendor(usage_page):
    return 0xFF00 <= usage_page <= 0xFFFF


def enumerate_devices(vid, pid):
    devs = hid.enumerate()
    out = [
        d
        for d in devs
        if d.get("vendor_id") == vid and d.get("product_id") == pid
    ]
    print(f"VID {vid:04X}:PID {pid:04X} 共 {len(out)} 个接口\n")
    if not out:
        print("没找到该设备。确认接收器已插好，或用 --all 看全部设备")
        return []

    header = f"{'interface':<10}{'usage_page':<12}{'usage':<9}{'产品名'}"
    print(header)
    print("-" * 52)
    for d in sorted(out, key=lambda x: (x.get("interface_number", -1), x.get("usage_page", 0))):
        mark = " *" if is_vendor(d.get("usage_page", 0)) else ""
        print(
            f"{str(d.get('interface_number', -1)):<10}"
            f"0x{d.get('usage_page', 0):04X}      "
            f"0x{d.get('usage', 0):04X}   "
            f"{d.get('product_string') or ''}{mark}"
        )
    print("\n* = 厂商自定义接口（电量最可能在这里）")
    return out


def listen(path, seconds, label):
    print(f"\n=== 被动监听 {label}  ({seconds}s) ===")
    try:
        dev = hid.device()
        dev.open_path(path)
    except Exception as e:
        print(f"  打开失败: {e}")
        return 0

    count = 0
    try:
        end = time.time() + seconds
        while time.time() < end:
            data = dev.read(64, timeout_ms=200)
            if data:
                count += 1
                print(f"  [{count:03d}] {hexs(data)}")
    except Exception as e:
        print(f"  读取异常: {e}")
    finally:
        dev.close()

    if count == 0:
        print("  期间没有任何上报（该接口不主动推送，需主动查询）")
    else:
        print(f"  共收到 {count} 帧")
    return count


def feature_read(path, report_id, label, size=64):
    """只读：尝试用 Feature Report 读取（不发送任何数据）。"""
    print(f"\n=== Feature 读取 {label}  report_id=0x{report_id:02X} ===")
    try:
        dev = hid.device()
        dev.open_path(path)
    except Exception as e:
        print(f"  打开失败: {e}")
        return
    try:
        resp = dev.get_feature_report(report_id, size)
        if resp:
            print(f"  回包({len(resp)}): {hexs(resp)}")
        else:
            print("  空回包")
    except Exception as e:
        print(f"  Feature 读取失败: {e}")
    finally:
        dev.close()


def send_frame(path, frame, label):
    print(f"\n=== 发送帧 {label} ===")
    print(f"  发送: {hexs(frame)}")
    try:
        dev = hid.device()
        dev.open_path(path)
    except Exception as e:
        print(f"  打开失败: {e}")
        return

    try:
        try:
            n = dev.write(frame)
            print(f"  write 返回 {n}")
        except Exception as e:
            print(f"  write 失败: {e}")
        data = dev.read(64, timeout_ms=800)
        if data:
            print(f"  回包: {hexs(data)}")
        else:
            print("  无回包")
    finally:
        dev.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vid", type=lambda s: int(s, 16), required=True, help="如 3554")
    ap.add_argument("--pid", type=lambda s: int(s, 16), required=True, help="如 FA09")
    ap.add_argument("--listen", type=int, default=0, help="被动监听秒数")
    ap.add_argument("--send", help="主动发送的帧（十六进制，空格分隔）")
    ap.add_argument("--feature", help="尝试 Feature 读取的 report id（如 13），只读不发数据")
    ap.add_argument("--all-iface", action="store_true", help="监听全部接口而非仅厂商接口")
    args = ap.parse_args()

    devs = enumerate_devices(args.vid, args.pid)
    if not devs:
        return

    if args.all_iface:
        targets = devs
    else:
        targets = [d for d in devs if is_vendor(d.get("usage_page", 0))]
        if not targets:
            print("\n没有厂商自定义接口，退化为监听全部接口")
            targets = devs

    if args.listen:
        print(f"\n监听目标 {len(targets)} 个接口，总计约 {args.listen * len(targets)} 秒")
        print("期间请正常敲键盘 / 动鼠标，让设备保持活跃")
        for d in targets:
            label = f"interface={d.get('interface_number')} usage_page=0x{d.get('usage_page', 0):04X}"
            listen(d["path"], args.listen, label)

    if args.feature:
        rid = int(args.feature, 16)
        for d in targets:
            label = f"interface={d.get('interface_number')} usage_page=0x{d.get('usage_page', 0):04X}"
            feature_read(d["path"], rid, label)

    if args.send:
        frame = bytes(int(x, 16) for x in args.send.replace(",", " ").split())
        for d in targets:
            label = f"interface={d.get('interface_number')} usage_page=0x{d.get('usage_page', 0):04X}"
            send_frame(d["path"], frame, label)


if __name__ == "__main__":
    main()
