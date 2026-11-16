"""逐帧特征的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass
class FrameFeature:
    """单帧的缩略特征。

    Attributes:
        index: 帧号（从 0 开始的整数，输入需可按其排序）。
        timestamp: 该帧的显示时间戳（秒），支持可变帧率。
        color: 缩略颜色特征向量，例如缩略图均值 RGB 或颜色直方图，
            取值范围 0-1 或 0-255（库内自动归一化）。
        edge: 可选边缘统计量（如边缘像素占比），与颜色特征同量级归一化。
    """

    index: int
    timestamp: float
    color: Sequence[float]
    edge: Optional[float] = None
