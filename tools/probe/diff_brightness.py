"""找出重力星球配置里的「亮度」字段。

原理：亮度从 100 改成 25 前后各读一次完整配置，逐字节对比。
旧快照已内置（亮度 100），脚本只负责读出当前值并做差异。

键盘休眠时会完全不回包，所以脚本会一直重试——运行期间请按住/连按键盘上的键，
或者慢慢转动旋钮，让它保持活跃。

用法:
    python diff_brightness.py
"""

import time

import hid

VID, PID = 0x3554, 0xFA09

OLD_RAW = {
    0: "13 44 0A 00 0E 00 03 03 02 00 00 04 04 07 00 01 20 01 00 A8",
    1: "13 44 0A 01 0E 01 00 00 00 01 00 02 01 00 FF 02 00 00 00 76",
    2: "13 44 0A 02 0E 01 00 04 02 00 00 00 00 00 00 00 00 00 00 78",
    3: "13 44 0A 03 0E 00 00 00 00 00 00 00 00 00 00 00 00 00 00 72",
    4: "13 44 0A 04 0E FF FF 04 00 04 36 04 17 04 10 04 36 04 36 52",
    5: "13 44 0A 05 0E 04 10 04 10 04 36 04 36 04 40 04 10 04 17 83",
    6: "13 44 0A 06 0E 04 36 04 17 04 17 04 17 04 36 04 36 04 36 AE",
}

OLD = {k: bytes(int(x, 16) for x in v.split()) for k, v in OLD_RAW.items()}
TIMEOUT_SECONDS = 150


def hexs(b):
    return " ".join(f"{x:02X}" for x in b)


def find_path():
    for d in hid.enumerate():
        if d["vendor_id"] == VID and d["product_id"] == PID and d["usage_page"] == 0xFF02:
            return d["path"]
    return None


def ask(dev, seq, pokes=3, wait_ms=1200):
    f = bytearray([0x13, 0x44, 0x01, seq] + [0] * 15 + [0])
    f[-1] = sum(f[:-1]) & 0xFF
    fb = bytes(f)
    for _ in range(pokes):
        dev.write(fb)
        time.sleep(0.1)
    r = dev.read(64, timeout_ms=wait_ms)
    if r and len(r) >= 20 and r[1] == 0x44 and r[3] == seq:
        return bytes(r)
    return None


def main():
    path = find_path()
    if not path:
        print("找不到 3554:FA09（0xFF02），确认接收器已插好")
        return

    dev = hid.device()
    dev.open_path(path)

    print("=" * 66)
    print("正在读取配置。键盘休眠时不会应答，请一边按键/转旋钮，一边等。")
    print(f"最长等待 {TIMEOUT_SECONDS} 秒。")
    print("=" * 66)

    new = {}
    start = time.time()
    try:
        while time.time() - start < TIMEOUT_SECONDS and len(new) < 10:
            progressed = False
            for seq in range(10):
                if seq in new:
                    continue
                r = ask(dev, seq)
                if r:
                    new[seq] = r
                    progressed = True
                    print(f"  seq{seq} ✓ {hexs(r)}")
                if time.time() - start >= TIMEOUT_SECONDS:
                    break
            if not progressed and len(new) < 10:
                print(f"  等待设备响应… 已读 {len(new)}/10  按任意键唤醒")
                time.sleep(0.5)
    finally:
        dev.close()

    print(f"\n共读到 {len(new)} 片\n")
    if not new:
        print("一片都没读到。确认键盘电源打开、处于 2.4G 模式且已连接。")
        return

    print("=== 逐字节差异（旧 = 亮度 100 / 新 = 亮度 25）===")
    diffs = []
    for seq in sorted(set(OLD) & set(new)):
        for i in range(20):
            if OLD[seq][i] != new[seq][i]:
                diffs.append((seq, i, OLD[seq][i], new[seq][i]))
                print(f"  seq{seq} [{i}]: 0x{OLD[seq][i]:02X}({OLD[seq][i]}) → 0x{new[seq][i]:02X}({new[seq][i]})")

    if not diffs:
        print("  无差异。可能：① 亮度没写进这 10 片  ② 键盘又休眠导致读到的是旧缓存")
        return

    print()
    for seq, i, o, n in diffs:
        if o == 100 and n == 25:
            print(f"  ★ seq{seq} 字节[{i}] 正好是 100 → 25，这就是亮度（按百分比直存）")
        elif 1 <= o <= 9 and 1 <= n <= 9:
            print(f"  ☆ seq{seq} 字节[{i}] 是 {o} → {n}，属于 0-9 档位，可能就是亮度档（25% ≈ 2 档）")


if __name__ == "__main__":
    main()
