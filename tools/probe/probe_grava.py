"""重力星球 / Compx 3554:FA09：候选字段判决实验。

`0x4A` 回包里的 [5] 恒为 100。亮度已从 100 改到 25 而它不动，排除了"亮度"。
现在要区分剩下两个候选：音量（0-100 旋钮值）还是电量。

脚本会按时间轴提示你做动作，只需照着做、看数字变不变。

用法:
    python probe_grava.py
"""

import time

import hid

VID, PID = 0x3554, 0xFA09

TIMELINE = [
    (0, "基线观察：什么都别动"),
    (8, ">>> 现在转动音量旋钮 / 按音量加减键"),
    (20, ">>> 停手。现在插上充电线"),
    (32, ">>> 拔掉充电线"),
    (42, ">>> 结束"),
]


def hexs(b):
    return " ".join(f"{x:02X}" for x in b)


def frame(cmd):
    f = bytearray([0x13, cmd, 0x01, 0x00] + [0] * 15 + [0])
    f[-1] = sum(f[:-1]) & 0xFF
    return bytes(f)


def find_path():
    for d in hid.enumerate():
        if d["vendor_id"] == VID and d["product_id"] == PID and d["usage_page"] == 0xFF02:
            return d["path"]
    return None


def main():
    path = find_path()
    if not path:
        print("找不到 3554:FA09 的 0xFF02 接口，确认接收器已插好")
        return

    dev = hid.device()
    dev.open_path(path)

    print("=" * 66)
    print("判决实验：区分 0x4A[5]=100 是「音量」还是「电量」")
    print("=" * 66)
    print("跟着提示做动作，只观察 [5] 和 [6] 变不变：")
    print("  · 转音量时跳变   → 是音量，不是电量")
    print("  · 插线时跳变/翻转 → 是电量/充电状态")
    print("  · 全程不动        → 既不是音量也不是电量，本项目放弃该设备\n")

    print(f"{'时间':<7}{'[4]':<6}{'[5]':<6}{'[6]':<6}回包")
    print("-" * 66)

    start = time.time()
    step = 0
    try:
        while True:
            elapsed = time.time() - start
            while step < len(TIMELINE) and elapsed >= TIMELINE[step][0]:
                if TIMELINE[step][0] > 0:
                    print(f"\n{'─' * 66}\n[T+{TIMELINE[step][0]}s] {TIMELINE[step][1]}\n{'─' * 66}")
                if TIMELINE[step][0] >= 42:
                    return
                step += 1

            while dev.read(64, timeout_ms=20):
                pass
            dev.write(frame(0x4A))
            time.sleep(0.05)
            r = dev.read(64, timeout_ms=500)

            if r and len(r) >= 7:
                raw = bytes(r)
                print(f"{elapsed:5.1f}s  {raw[4]:<6}{raw[5]:<6}{raw[6]:<6}{hexs(raw)}")
            else:
                print(f"{elapsed:5.1f}s  无回包")
            time.sleep(0.9)
    except KeyboardInterrupt:
        print("\n已中断")
    finally:
        dev.close()


if __name__ == "__main__":
    main()
