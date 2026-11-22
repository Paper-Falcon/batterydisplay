"""控制面板（独立进程）。

为什么单独一个进程：pystray 的 icon.run() 会占住主线程，
再往里塞 Tkinter 的 mainloop 很容易互相卡死。开成独立进程最省心，
配置通过 settings.json 传递，主程序监听文件变化自动生效。
"""

import os
import sys
import tkinter as tk
from tkinter import ttk

import settings

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "BatteryDisplay"


# ─────────────── 开机自启（注册表） ───────────────

def autostart_command():
    pyw = sys.executable
    if pyw.lower().endswith("python.exe"):
        pyw = pyw[: -len("python.exe")] + "pythonw.exe"
    app = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
    return f'"{pyw}" "{app}"'


def get_autostart():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, VALUE_NAME)
            return bool(val)
    except OSError:
        return False


def set_autostart(enabled):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if enabled:
            winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, VALUE_NAME)
            except OSError:
                pass


# ─────────────── 界面 ───────────────

class Panel:
    def __init__(self, root):
        self.root = root
        self.cfg = settings.load()

        root.title("BatteryDisplay 设置")
        root.resizable(False, False)
        root.attributes("-topmost", True)
        root.after(600, lambda: root.attributes("-topmost", False))

        frm = ttk.Frame(root, padding=16)
        frm.grid(row=0, column=0, sticky="nsew")
        row = 0

        def section(title):
            nonlocal row
            lbl = ttk.Label(frm, text=title, font=("Microsoft YaHei UI", 9, "bold"))
            lbl.grid(row=row, column=0, columnspan=3, sticky="w", pady=(10 if row else 0, 6))
            row += 1

        def check(text, var):
            nonlocal row
            ttk.Checkbutton(frm, text=text, variable=var).grid(
                row=row, column=0, columnspan=3, sticky="w", pady=2)
            row += 1

        def spin(text, var, lo, hi, step=1, unit=""):
            nonlocal row
            ttk.Label(frm, text=text).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Spinbox(frm, from_=lo, to=hi, increment=step, textvariable=var,
                        width=8).grid(row=row, column=1, sticky="w", padx=(10, 4))
            ttk.Label(frm, text=unit).grid(row=row, column=2, sticky="w")
            row += 1

        # 显示
        section("显示")
        self.v_overlay = tk.BooleanVar(value=self.cfg["overlay_enabled"])
        check("在任务栏显示电量", self.v_overlay)
        self.v_font = tk.IntVar(value=self.cfg["font_size"])
        spin("字号", self.v_font, 11, 24, 1, "px")

        # 刷新
        section("刷新")
        self.v_refresh = tk.IntVar(value=self.cfg["refresh_seconds"])
        spin("读取间隔", self.v_refresh, 10, 600, 10, "秒（越短越耗电）")

        # 提醒
        section("低电量提醒")
        self.v_alert = tk.BooleanVar(value=self.cfg["alerts_enabled"])
        check("电量过低时弹通知", self.v_alert)

        ths = list(self.cfg["alert_thresholds"]) + [0, 0]
        self.v_high = tk.IntVar(value=ths[0])
        self.v_low = tk.IntVar(value=ths[1])
        ttk.Label(frm, text="提醒阈值").grid(row=row, column=0, sticky="w", pady=2)
        box = ttk.Frame(frm)
        box.grid(row=row, column=1, columnspan=2, sticky="w", padx=(10, 0))
        ttk.Spinbox(box, from_=2, to=100, textvariable=self.v_high, width=6).pack(side="left")
        ttk.Label(box, text="% 与").pack(side="left", padx=4)
        ttk.Spinbox(box, from_=1, to=99, textvariable=self.v_low, width=6).pack(side="left")
        ttk.Label(box, text="%").pack(side="left", padx=4)
        row += 1

        # 启动
        section("启动")
        self.v_auto = tk.BooleanVar(value=get_autostart())
        check("开机自动启动", self.v_auto)

        ttk.Separator(frm, orient="horizontal").grid(
            row=row, column=0, columnspan=3, sticky="ew", pady=(14, 10))
        row += 1

        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=3, sticky="e")
        ttk.Button(btns, text="取消", command=self.root.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="保存", command=self.save).pack(side="right")

        row += 1
        ttk.Label(frm, text=f"配置文件：{settings.path()}",
                  foreground="#777").grid(row=row, column=0, columnspan=3,
                                          sticky="w", pady=(10, 0))

    def save(self):
        high, low = int(self.v_high.get()), int(self.v_low.get())
        if high <= low:          # 阈值写反了就自动纠正，不让用户卡在这
            high, low = low + 1, low

        data = dict(self.cfg)
        data.update({
            "overlay_enabled": bool(self.v_overlay.get()),
            "font_size": int(self.v_font.get()),
            "refresh_seconds": int(self.v_refresh.get()),
            "alerts_enabled": bool(self.v_alert.get()),
            "alert_thresholds": [high, low],
        })
        settings.save(data)

        try:
            set_autostart(bool(self.v_auto.get()))
        except Exception as e:
            print(f"写注册表失败: {e}")

        self.root.destroy()


def main():
    # 与主程序一致：声明 DPI 感知，否则高缩放下界面会被系统拉伸发虚
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    root = tk.Tk()
    Panel(root)
    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 3}")
    root.mainloop()


if __name__ == "__main__":
    main()
