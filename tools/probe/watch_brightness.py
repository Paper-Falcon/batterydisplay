"""盯住亮度字段：确认档位编码方式。

已知：seq4 的第 7 字节在亮度 100 时为 4、亮度 25 时为 1。
待确认：第 8 字节（100→0x00 / 25→0x10）是"速度位"还是同一个 16 位值的高低位。

方法：只轮询 seq4（单分片，速度快、成功率高），你在键盘上把亮度依次拨过
0 → 25 → 50 → 75 → 100，每档停 3 秒。看第 7 字节是否走 0/1/2/3/4。

用法:
    python watch_brightness.py            # 盯 60 秒
    按住 Shift 保持键盘唤醒
"""

import time

import hid

VID, PID = 0x3554, 0xFA09
SEQ = 4


def find_path():
    for d in hid.enumerate():
        if d["vendor_id"] == VID and d["product_id"] == PID and d["usage_page"] == 0xFF02:
            return d["path"]
    return None


def ask(dev, seq, wait_ms=800):
    f = bytearray([0x13, 0x44, 0x01, seq] + [0] * 15 + [0])
    f[-1] = sum(f[:-1]) & 0xFF
    fb = bytes(f)
    for _ in range(3):
        try:
            dev.write(fb)
        except Exception:
            return None
        time.sleep(0.09)
    try:
        r = dev.read(64, timeout_ms=wait_ms)
    except Exception:
        return None
    if r and len(r) >= 20 and r[1] == 0x44 and r[3] == seq:
        return bytes(r)
    return None


def main():
    path = find_path()
    if not path:
        raise SystemExit("找不到 3554:FA09（0xFF02）")

    dev = hid.device()
    dev.open_path(path)

    print("=" * 62)
    print("请按住 Shift，然后依次把亮度拨到：0 → 25 → 50 → 75 → 100")
    print("每档停 3 秒左右。盯 [7] 列是否走 0/1/2/3/4。")
    print("=" * 62)
    print(f"{'时间':<7}{'[7]':<6}{'[8]':<6}{'16位BE':<9}{'档位推断'}")
    print("-" * 62)

    start = time.time()
    try:
        while time.time() - start < 60:
            r = ask(dev, SEQ)
            if not r:
                print(f"{time.time()-start:5.1f}s  无回包")
                time.sleep(0.8)
                continue

            b7, b8 = r[7], r[8]
            val = (b7 << 8) | b8
            if 0 <= b7 <= 4:
                guess = f"档位 {b7} → {b7 * 25}%"
            else:
                guess = "超出 0-4，可能是 16 位值"
            print(f"{time.time()-start:5.1f}s  {b7:<6}{b8:<6}0x{val:04X}   {guess}")
            time.sleep(0.7)
    except KeyboardInterrupt:
        print("\n已中断")
    finally:
        dev.close()


if __name__ == "__main__":
    main()
