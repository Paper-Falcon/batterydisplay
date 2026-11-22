"""核心逻辑测试（不需要真实硬件）。

运行： python -m unittest discover -s tests -v
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "prototype"))

import engine
import overlay
import settings


class LevelColorTest(unittest.TestCase):
    """低电量配色：颜色要稀缺才有信号意义。"""

    def test_正常电量用白色(self):
        self.assertEqual(overlay._level_color(57, (20, 10)), overlay.FG)
        self.assertEqual(overlay._level_color(21, (20, 10)), overlay.FG)

    def test_越过较高阈值变琥珀(self):
        self.assertEqual(overlay._level_color(20, (20, 10)), overlay.WARN)
        self.assertEqual(overlay._level_color(18, (20, 10)), overlay.WARN)

    def test_越过最低阈值变红(self):
        self.assertEqual(overlay._level_color(10, (20, 10)), overlay.DANGER)
        self.assertEqual(overlay._level_color(3, (20, 10)), overlay.DANGER)

    def test_未知电量不着色(self):
        self.assertEqual(overlay._level_color(None, (20, 10)), overlay.FG)

    def test_阈值顺序颠倒也能用(self):
        self.assertEqual(overlay._level_color(8, (10, 20)), overlay.DANGER)


class SettingsTest(unittest.TestCase):
    def test_越界值被夹紧(self):
        d = settings._sanitize({**settings.DEFAULTS,
                                "refresh_seconds": 1, "font_size": 99})
        self.assertEqual(d["refresh_seconds"], 10)
        self.assertEqual(d["font_size"], 24)

    def test_阈值去重排序并剔除非法(self):
        d = settings._sanitize({**settings.DEFAULTS,
                                "alert_thresholds": [10, 20, 10, 999, "x", -5]})
        self.assertEqual(d["alert_thresholds"], [20, 10])

    def test_空阈值回落到默认(self):
        d = settings._sanitize({**settings.DEFAULTS, "alert_thresholds": []})
        self.assertEqual(d["alert_thresholds"], settings.DEFAULTS["alert_thresholds"])


class BuildFrameTest(unittest.TestCase):
    """用真实设备包验证帧构造，含校验和。"""

    def _pack(self, name):
        path = os.path.join(engine.PACK_DIR, name)
        with open(path, encoding="utf-8") as f:
            return json.load(f)["devices"][0]

    def test_狼蛛_33字节且校验正确(self):
        entry = self._pack("aula-f108-pro.json")
        frame = engine.build_frame(entry["read"], entry["transport"])
        self.assertEqual(len(frame), 33)
        self.assertEqual(frame[:3], bytes([0x00, 0x20, 0x01]))
        self.assertEqual(frame[-1], sum(frame[:-1]) & 0xFF)

    def test_ATK_补齐到64字节且无校验(self):
        entry = self._pack("atk-a9-plus.json")
        frame = engine.build_frame(entry["read"], entry["transport"])
        self.assertEqual(len(frame), 64)
        self.assertEqual(frame[:5], bytes([0x08, 0x7D, 0x72, 0x02, 0x00]))


class _FakeIcon:
    def __init__(self):
        self.sent = []

    def notify(self, message, title=None):
        self.sent.append(message)


def _bare_app():
    """绕过 __init__，避免测试时去枚举真实硬件。"""
    app_mod = __import__("app")
    a = app_mod.App.__new__(app_mod.App)
    a.cfg = dict(settings.DEFAULTS)
    a.icon = _FakeIcon()
    a.state = {}
    return a


class AlertTest(unittest.TestCase):
    def _st(self, level, status="ok"):
        return {"name": "测试鼠标", "kind": "mouse", "level": level,
                "status": status, "detail": "", "ts": 0, "alerted": set()}

    def test_越过高阈值弹一次且不重复(self):
        a = _bare_app()
        st = self._st(21)
        a.check_alert(st)
        self.assertEqual(len(a.icon.sent), 0)

        st["level"] = 20
        a.check_alert(st)
        st["level"] = 19
        a.check_alert(st)
        self.assertEqual(len(a.icon.sent), 1)

    def test_一次掉穿两阈值只弹一条(self):
        a = _bare_app()
        st = self._st(25)
        a.check_alert(st)
        st["level"] = 5
        a.check_alert(st)
        self.assertEqual(len(a.icon.sent), 1)
        self.assertEqual(st["alerted"], {20, 10})

    def test_充电回升后重新武装(self):
        a = _bare_app()
        st = self._st(18)
        a.check_alert(st)
        self.assertEqual(len(a.icon.sent), 1)

        st["level"] = 100
        a.check_alert(st)
        self.assertEqual(st["alerted"], set())

        st["level"] = 15
        a.check_alert(st)
        self.assertEqual(len(a.icon.sent), 2)

    def test_休眠时不提醒(self):
        a = _bare_app()
        st = self._st(5, status="silent")
        a.check_alert(st)
        self.assertEqual(len(a.icon.sent), 0)

    def test_关闭提醒后不弹(self):
        a = _bare_app()
        a.cfg["alerts_enabled"] = False
        a.check_alert(self._st(5))
        self.assertEqual(len(a.icon.sent), 0)


class StripItemsTest(unittest.TestCase):
    def _st(self, kind, level, status):
        return {"name": kind, "kind": kind, "level": level,
                "status": status, "detail": "", "ts": 0, "alerted": set()}

    def test_离线设备不显示(self):
        a = _bare_app()
        a.state = {"k": self._st("keyboard", 88, "offline"),
                   "m": self._st("mouse", 58, "ok")}
        self.assertEqual(a.strip_items(), [("mouse", 58, False)])

    def test_休眠设备保留读数并置灰(self):
        a = _bare_app()
        a.state = {"k": self._st("keyboard", 88, "silent")}
        self.assertEqual(a.strip_items(), [("keyboard", 88, True)])

    def test_全部离线时无项可显示(self):
        a = _bare_app()
        a.state = {"k": self._st("keyboard", 88, "offline")}
        self.assertEqual(a.strip_items(), [])

    def test_最低电量不计入离线设备(self):
        a = _bare_app()
        a.state = {"k": self._st("keyboard", 5, "offline"),
                   "m": self._st("mouse", 58, "ok")}
        self.assertEqual(a.lowest_level(), 58)


class PlaceTest(unittest.TestCase):
    """回归：位置没变也必须把新画面推上去，否则电量会一直卡在旧值。"""

    class _StubOverlay:
        def __init__(self):
            self.visible = False
            self.shown = []

        def show(self, img, x, y):
            self.shown.append((getattr(img, "tag", None), x, y))
            self.visible = True

        def hide(self):
            self.visible = False

    def _app(self):
        a = _bare_app()
        a.overlay = self._StubOverlay()
        a.cached_img = None
        a.cached_pos = None
        a.img_version = 0
        a.placed_version = -1
        return a

    METRICS = {"tray_left": 2283, "left": 0, "width": 2560, "top": 1392,
               "height": 48, "apps_right": 1346, "dpi_scale": 1.0,
               "hidden": False, "visible": True}

    def _img(self, tag):
        from PIL import Image
        im = Image.new("RGBA", (89, 26), (0, 0, 0, 0))
        im.tag = tag
        return im

    def test_位置不变时新画面仍会推送(self):
        a = self._app()

        a.img_version += 1
        a.place(self._img("first"), self.METRICS)
        self.assertEqual(len(a.overlay.shown), 1)

        a.img_version += 1                 # 电量变了，重新渲染
        a.place(self._img("second"), self.METRICS)
        self.assertEqual(len(a.overlay.shown), 2)
        self.assertEqual(a.overlay.shown[-1][0], "second")

    def test_同版本重复调用才跳过(self):
        a = self._app()
        img = self._img("same")

        a.img_version += 1
        a.place(img, self.METRICS)
        a.place(img, self.METRICS)          # 版本没涨 → 跳过
        self.assertEqual(len(a.overlay.shown), 1)

    def test_位置变化会重新推送(self):
        a = self._app()
        a.img_version += 1
        a.place(self._img("a"), self.METRICS)
        moved = dict(self.METRICS, tray_left=2200)
        a.place(self._img("a"), moved)
        self.assertEqual(len(a.overlay.shown), 2)


if __name__ == "__main__":
    unittest.main()
