"""HID 接口枚举 + ATK 已知查询帧验证。

用法:
    python probe_atk.py            # 只枚举接口（只读，不发任何帧）
    python probe_atk.py --send     # 枚举后对厂商接口试发已知查询帧
"""

import argparse
import sys
import time

try:
    import hid
except ImportError:
    sys.exit("缺少依赖: pip install hidapi")

# 社区已验证的 ATK/VXE 无线鼠标协议（VID 373B:1216）
# usage page 0xFF05, Report ID 0x08, 回包第 7 字节为电量
KNOWN_QUERY = bytes([0x08, 0x7D, 0x72, 0x02, 0x00, 0x01, 0x07, 0x01])
TARGET_VID = 0x373B
TARGET_PID = 0x1216


def hexs(data):
    return " ".join(f"{b:02X}" for b in data)


def enumerate_hid(show_all=False):
    devices = hid.enumerate()
    print(f"共枚举到 {len(devices)} 个 HID 接口\n")

    rows = []
    for d in devices:
        vid, pid = d.get("vendor_id", 0), d.get("product_id", 0)
        if not show_all and vid != TARGET_VID:
            continue
        rows.append(d)

    if not rows:
        print(f"没有找到 VID={TARGET_VID:04X} 的设备")
        print("请确认 2.4G 接收器已插好，并用 --all 查看全部")
        return []

    header = f"{'VID:PID':<10} {'界面':<5} {'usage_page':<11} {'usage':<7} {'产品名'}"
    print(header)
    print("-" * len(header))
    for d in sorted(rows, key=lambda x: (x.get("product_id", 0), x.get("interface_number", -1))):
        print(
            f"{d.get('vendor_id', 0):04X}:{d.get('product_id', 0):04X}"
            f"  {str(d.get('interface_number', -1)):<5}"
            f" 0x{d.get('usage_page', 0):04X}      "
            f"0x{d.get('usage', 0):04X}   "
            f"{d.get('product_string') or d.get('manufacturer_string') or ''}"
        )
    print()
    return rows


def pick_vendor_interfaces(rows):
    """厂商自定义接口的 usage_page 在 0xFF00~0xFFFF 之间。"""
    out = []
    for d in rows:
        up = d.get("usage_page", 0)
        if 0xFF00 <= up <= 0xFFFF:
            out.append(d)
    return out


def try_read(path, label=""):
    print(f"\n=== 尝试 {label} ===")
    try:
        dev = hid.device()
        dev.open_path(path)
    except Exception as e:
        print(f"  打开失败: {e}")
        return

    try:
        # 方式一：Feature Report
        try:
            n = dev.send_feature_report(KNOWN_QUERY)
            print(f"  send_feature_report → 写入 {n} 字节: {hexs(KNOWN_QUERY)}")
            resp = dev.get_feature_report(KNOWN_QUERY[0], 64)
            print(f"  get_feature_report  → 回包({len(resp)}): {hexs(resp)}")
            report_battery(resp)
        except Exception as e:
            print(f"  Feature Report 失败: {e}")

        # 方式二：Output + Input（中断传输）
        try:
            n = dev.write(KNOWN_QUERY)
            print(f"  write → 写入 {n} 字节: {hexs(KNOWN_QUERY)}")
            data = dev.read(64, timeout_ms=800)
            if data:
                print(f"  read  → 回包({len(data)}): {hexs(data)}")
                report_battery(data)
            else:
                print("  read  → 超时，无回包")
        except Exception as e:
            print(f"  Output/Input 失败: {e}")
    finally:
        dev.close()


def report_battery(resp):
    if len(resp) > 7:
        val = resp[7]
        mark = "  <-- 电量候选" if 1 <= val <= 100 else "  (不在 1-100 范围，可能不是电量)"
        print(f"  第 7 字节 = {val} ({val:#04x}){mark}")
    if len(resp) > 3:
        print(f"  前 4 字节 = {hexs(resp[:4])}  （协议头，应与查询帧呼应）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="对厂商接口试发已知查询帧")
    ap.add_argument("--all", action="store_true", help="枚举全部 HID 设备")
    ap.add_argument("--repeat", type=int, default=1, help="连续采样次数（验证稳定性）")
    args = ap.parse_args()

    rows = enumerate_hid(show_all=args.all)
    if not rows:
        return

    vendor = pick_vendor_interfaces(rows)
    print(f"厂商自定义接口: {len(vendor)} 个")
    for d in vendor:
        print(
            f"  interface={d.get('interface_number')} "
            f"usage_page=0x{d.get('usage_page', 0):04X} "
            f"usage=0x{d.get('usage', 0):04X}"
        )

    if not args.send:
        print("\n（只读模式，未发送任何帧。加 --send 试发查询帧）")
        return

    if not vendor:
        print("没有厂商自定义接口，无法试发")
        return

    # 优先 usage_page 0xFF05（已知协议），否则全部试一遍
    ordered = sorted(vendor, key=lambda d: 0 if d.get("usage_page") == 0xFF05 else 1)

    if args.repeat > 1:
        target = ordered[0]
        label = f"interface={target.get('interface_number')} usage_page=0x{target.get('usage_page', 0):04X}"
        for i in range(args.repeat):
            print(f"\n--- 第 {i + 1}/{args.repeat} 次 ---")
            try_read(target["path"], label)
            if i < args.repeat - 1:
                time.sleep(1)
        return

    for d in ordered:
        try_read(
            d["path"],
            f"interface={d.get('interface_number')} usage_page=0x{d.get('usage_page', 0):04X}",
        )


if __name__ == "__main__":
    main()
