"""游戏录屏镜头切换索引库。

输入逐帧缩略颜色特征（可选边缘统计）与时间戳，输出镜头区间、
帧号和时间码。检测流程：候选生成 -> 闪光抑制 -> 渐变趋势归并 ->
最短时长过滤。
"""

from .features import FrameFeature
from .indexer import (
    IndexerConfig,
    Shot,
    ShotBoundary,
    ShotIndex,
    ShotIndexer,
)
from .timecode import format_timecode

__all__ = [
    "FrameFeature",
    "IndexerConfig",
    "Shot",
    "ShotBoundary",
    "ShotIndex",
    "ShotIndexer",
    "format_timecode",
]
