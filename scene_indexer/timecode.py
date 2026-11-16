"""时间码格式化。"""

from __future__ import annotations


def format_timecode(seconds: float) -> str:
    """把秒数格式化为 ``HH:MM:SS.mmm`` 时间码。"""
    if seconds < 0:
        raise ValueError("seconds must be non-negative")
    total_ms = int(round(seconds * 1000.0))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
