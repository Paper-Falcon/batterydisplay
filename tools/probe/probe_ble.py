"""蓝牙 BLE 电量探测：标准 GATT Battery Service (0x180F / 0x2A19)。

用于验证「三模键盘/鼠标切到蓝牙模式后，能否用标准协议读到电量」。
如果这条路通，就不需要逆向 2.4G 私有协议。

用法:
    python probe_ble.py                 # 扫描并列出所有 BLE 设备
    python probe_ble.py --name F108     # 只处理名称匹配的设备
    python probe_ble.py --all           # 对全部设备尝试读电池
"""

import argparse
import asyncio
import sys

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    sys.exit("缺少依赖: python -m pip install bleak")

BATTERY_SERVICE = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"


async def scan():
    print("扫描 BLE 设备（约 6 秒）…\n")
    devices = await BleakScanner.discover(timeout=6.0)
    if not devices:
        print("未发现 BLE 设备")
        return []
    print(f"{'名称':<28}{'地址':<20}{'RSSI'}")
    print("-" * 58)
    for d in sorted(devices, key=lambda x: (x.name or "zzz")):
        rssi = getattr(d, "rssi", None)
        if rssi is None:
            adv = getattr(d, "advertisement_data", None)
            rssi = getattr(adv, "rssi", None) if adv else None
        print(f"{(d.name or '(未命名)'):<28}{d.address:<20}{rssi if rssi is not None else '-'}")
    return devices


async def read_battery(device):
    print(f"\n=== {device.name or device.address} ({device.address}) ===")
    try:
        async with BleakClient(device) as client:
            if not client.is_connected:
                print("  连接失败")
                return
            print("  已连接，枚举服务…")
            has_battery = False
            for svc in client.services:
                if svc.uuid.lower() == BATTERY_SERVICE:
                    has_battery = True
                    print(f"  ✓ 发现电池服务 {svc.uuid}")
                    for ch in svc.characteristics:
                        print(f"      char {ch.uuid}  props={ch.properties}")
                elif "180f" in svc.uuid.lower():
                    print(f"  ? 可疑服务 {svc.uuid}")

            if not has_battery:
                print("  ✗ 没有标准电池服务 (0x180F)")
                return

            data = await client.read_gatt_char(BATTERY_LEVEL)
            level = data[0]
            print(f"\n  >>> 电量 = {level}%")

            try:
                await client.start_notify(
                    BATTERY_LEVEL,
                    lambda _c, d: print(f"  [通知] 电量变化 → {d[0]}%"),
                )
                print("  已订阅电量变化通知，监听 10 秒…")
                await asyncio.sleep(10)
            except Exception as e:
                print(f"  订阅通知失败（不影响读数）: {e}")
    except Exception as e:
        print(f"  连接/读取失败: {type(e).__name__}: {e}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", help="只处理名称包含该字符串的设备")
    ap.add_argument("--all", action="store_true", help="对全部设备尝试读电池")
    args = ap.parse_args()

    devices = await scan()
    if not devices:
        return

    if args.name:
        targets = [d for d in devices if args.name.lower() in (d.name or "").lower()]
    elif args.all:
        targets = devices
    else:
        print("\n只扫描不连接。加 --name <关键字> 或 --all 才会尝试读取电量。")
        return

    if not targets:
        print(f"\n没有名称匹配「{args.name}」的设备")
        return

    print(f"\n准备连接 {len(targets)} 台设备…")
    for d in targets:
        await read_battery(d)


if __name__ == "__main__":
    asyncio.run(main())
