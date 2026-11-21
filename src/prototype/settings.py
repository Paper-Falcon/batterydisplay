"""配置读写：主程序与控制面板共用。

配置文件放在 %LOCALAPPDATA%\\BatteryDisplay\\settings.json，
这样程序目录保持只读，也避免把个人配置提交进仓库。
"""

import json
import os

DEFAULTS = {
    "refresh_seconds": 60,      # 轮询间隔
    "overlay_enabled": True,    # 是否在任务栏显示
    "font_size": 15,            # 任务栏文字字号
    "alerts_enabled": True,     # 低电量提醒
    "alert_thresholds": [20, 10],
}

LIMITS = {
    "refresh_seconds": (10, 600),
    "font_size": (11, 24),
}


def _dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "BatteryDisplay")


def path():
    return os.path.join(_dir(), "settings.json")


def load():
    data = dict(DEFAULTS)
    try:
        with open(path(), encoding="utf-8") as f:
            data.update(json.load(f))
    except Exception:
        pass
    return _sanitize(data)


def _sanitize(data):
    for key, (lo, hi) in LIMITS.items():
        try:
            data[key] = max(lo, min(hi, int(data.get(key, DEFAULTS[key]))))
        except (TypeError, ValueError):
            data[key] = DEFAULTS[key]

    data["overlay_enabled"] = bool(data.get("overlay_enabled", True))
    data["alerts_enabled"] = bool(data.get("alerts_enabled", True))

    ths = data.get("alert_thresholds") or DEFAULTS["alert_thresholds"]
    cleaned = []
    for t in ths:
        try:
            v = int(t)
        except (TypeError, ValueError):
            continue
        if 1 <= v <= 100:
            cleaned.append(v)
    data["alert_thresholds"] = sorted(set(cleaned), reverse=True) or list(
        DEFAULTS["alert_thresholds"])
    return data


def save(data):
    data = _sanitize(data)
    os.makedirs(_dir(), exist_ok=True)
    tmp = path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path())
    return data
