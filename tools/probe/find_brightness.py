"""定位重力星球配置里的「亮度」字节（严格单变量对比）。

方法：连续读三次快照，中间只让你改一个东西——亮度。
    A：当前状态
    B：亮度调到 100
    C：亮度调到 25
A 与 C 应一致（都是 25），B 不同。B 与 C 不同的那几个字节里，
差值方向与 100→25 相符的那个就是亮度。

重要：读取时键盘必须在活跃状态。脚本会提示你按住 Shift（不改变任何设置）。

用法:
    python find_brightness.py
"""

import sys
import time

import hid

VID, PID = 0x3554, 0xFA09
FRAGMENTS = 10


def hexs(b):
    return " ".join(f"{x:02X}" for x in b)


def find_path():
    for d in hid.enumerate():
        if d["vendor_id"] == VID and d["product_id"] == PID and d["usage_page"] == 0xFF02:
            return d["path"]
    return None


def ask(dev, seq, pokes=3, wait_ms=900):
    f = bytearray([0x13, 0x44, 0x01, seq] + [0] * 15 + [0])
    f[-1] = sum(f[:-1]) & 0xFF
    fb = bytes(f)
    for _ in range(pokes):
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


def snapshot(dev, name):
    print(f"\n--- 读取快照 {name}（键盘休眠时请按住 Shift）---")
    frags = {}
    for seq in range(FRAGMENTS):
        for _ in range(5):
            r = ask(dev, seq)
            if r:
                frags[seq] = r
                break
        mark = "✓" if seq in frags else "✗"
        print(f"  seq{seq} {mark}  {hexs(frags[seq]) if seq in frags else ''}")
    print(f"  共 {len(frags)}/{FRAGMENTS} 片")
    return frags


def diff(a, b):
    out = {}
    for seq in sorted(set(a) & set(b)):
        for i in range(1, 19):
            if a[seq][i] != b[seq][i]:
                out.setdefault(seq, []).append((i, a[seq][i], b[seq][i]))
    return out


def main():
    path = find_path()
    if not path:
        sys.exit("找不到 3554:FA09（0xFF02），确认接收器已插好")

    dev = hid.device()
    dev.open_path(path)

    print("=" * 68)
    print("定位亮度字节：三次快照对比")
    print("=" * 68)
    print("全程请按住 Shift 键（不会改变任何设置），让键盘保持唤醒。\n")

    try:
        input("【当前亮度不变】按回车开始读 A …")
        A = snapshot(dev, "A")

        input("\n【请把亮度调到 100】调好后按回车读 B …")
        time.sleep(1.5)
        B = snapshot(dev, "B")

        input("\n【请把亮度调到 25】调好后按回车读 C …")
        time.sleep(1.5)
        C = snapshot(dev, "C")
    finally:
        dev.close()

    print("\n" + "=" * 68)
    print("结果")
    print("=" * 68)

    d_ac = diff(A, C)
    if d_ac:
        print("⚠ A 与 C 不一致（都应是 25），说明期间有别的设置被改过，结论可能不准：")
        for seq, items in d_ac.items():
            for i, x, y in items:
                print(f"    seq{seq}[{i}] A=0x{x:02X} C=0x{y:02X}")
    else:
        print("✓ A 与 C 完全一致，变量干净。")

    d_bc = diff(B, C)

    # 关键：缺分片会让对比静默漏掉证据，必须显式报出来
    missing_c = sorted((set(A) | set(B)) - set(C))
    if missing_c:
        print(f"\n⚠ 快照 C 缺少分片 {missing_c}，这些分片没有参与对比 —— 结论不可信。")
        print("  请重跑：读取时按住 Shift，或改完设置后立刻多按几次键再回车。")
        for seq in missing_c:
            for src_name, src in (("A", A), ("B", B)):
                if seq in src:
                    print(f"  seq{seq} 在快照 {src_name} 里是: {hexs(src[seq])}")

    if not d_bc:
        scope = "共有分片内" if missing_c else "全部分片内"
        print(f"\nB 与 C 在{scope}无差异 —— 亮度没有存放在已读到的分片里。")
        return

    print("\nB(亮度100) 与 C(亮度25) 的差异：")
    cands = []
    for seq, items in d_bc.items():
        for i, x, y in items:
            role = ""
            if x == 100 and y == 25:
                role = "  ★★ 直接是 100→25，就是亮度（百分比直存）"
                cands.append((seq, i, "high"))
            elif 1 <= x <= 9 and 1 <= y <= 9:
                role = "  ☆ 0-9 档位，可能是亮度档（25%≈2 档）"
                cands.append((seq, i, "mid"))
            print(f"  seq{seq}[{i}]: 0x{x:02X}({x}) → 0x{y:02X}({y}){role}")

    if cands:
        print("\n候选字段：")
        for seq, i, level in cands:
            tag = "高置信" if level == "high" else "中置信（档位制）"
            print(f"  seq{seq} 第 {i} 字节  [{tag}]")
    else:
        print("\n没有明显候选。这些差异可能是亮度换算后的档位，或亮度根本不在配置区。")


if __name__ == "__main__":
    main()
