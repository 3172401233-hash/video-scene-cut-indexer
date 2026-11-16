"""镜头切换索引核心逻辑。

处理流程：
1. 候选生成：相邻帧颜色（+可选边缘）距离超过硬切阈值的位置成为候选。
2. 闪光抑制：内容在 1~max_flash_frames 帧内“离开又回来”的孤立尖峰
   （闪光弹白帧、两帧黑帧）被整段剔除。
3. 渐变趋势归并：连续超过软阈值的区间若持续足够久且区间首尾内容
   差异足够大（单调渐变趋势），归并为一个渐变边界，而不是一串碎切。
4. 最短时长过滤：短于 min_shot_duration 的镜头并入相邻镜头，
   合并时优先保留强度更大的边界。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from .features import FrameFeature
from .timecode import format_timecode


@dataclass
class IndexerConfig:
    """检测阈值配置。

    所有距离均为归一化到 [0, 1] 的均值绝对差。
    """

    cut_threshold: float = 0.45
    """硬切阈值：相邻帧距离达到该值视为硬切候选。"""

    gradual_threshold: float = 0.015
    """软阈值：相邻帧距离达到该值视为渐变过程的一部分。"""

    gradual_min_duration: float = 0.4
    """渐变区间最短持续秒数（按时间戳计算，兼容可变帧率）。"""

    flash_recovery_threshold: float = 0.15
    """闪光抑制：闪光前后内容距离低于该值认为“内容恢复”。"""

    max_flash_frames: int = 2
    """闪光帧的最大长度（帧），覆盖单帧闪白与两帧闪黑。"""

    min_shot_duration: float = 0.5
    """镜头最短持续秒数，更短的镜头被并入相邻镜头。"""

    edge_weight: float = 0.3
    """提供边缘统计时，边缘距离在综合距离中的权重。"""


@dataclass
class ShotBoundary:
    """一个镜头边界。frame 为新镜头的第一帧。"""

    frame: int
    timestamp: float
    kind: str  # "hard_cut" 或 "gradual"
    strength: float

    @property
    def timecode(self) -> str:
        return format_timecode(self.timestamp)


@dataclass
class Shot:
    """一个镜头区间，帧号为闭区间 [start_frame, end_frame]。"""

    index: int
    start_frame: int
    end_frame: int
    start_time: float
    end_time: float

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    @property
    def start_timecode(self) -> str:
        return format_timecode(self.start_time)

    @property
    def end_timecode(self) -> str:
        return format_timecode(self.end_time)


@dataclass
class ShotIndex:
    """索引结果：镜头区间列表 + 边界列表。"""

    shots: List[Shot]
    boundaries: List[ShotBoundary]


def _mean_abs_diff(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / max(len(a), 1)


class ShotIndexer:
    """镜头索引器。用法：``ShotIndexer(config).index(frames)``。"""

    def __init__(self, config: Optional[IndexerConfig] = None):
        self.config = config or IndexerConfig()

    def index(self, frames: Sequence[FrameFeature]) -> ShotIndex:
        if not frames:
            raise ValueError("frames must be non-empty")
        frames = sorted(frames, key=lambda f: f.index)
        for prev, cur in zip(frames, frames[1:]):
            if cur.index == prev.index:
                raise ValueError(f"duplicate frame index: {cur.index}")
            if cur.timestamp < prev.timestamp:
                raise ValueError("timestamps must be non-decreasing")

        colors, edges = self._normalized(frames)
        timestamps = [f.timestamp for f in frames]
        numbers = [f.index for f in frames]
        count = len(frames)

        # 1. 相邻帧综合距离，dist[p] 表示边界 p（frames[p] 之前）的距离。
        dist = [0.0] * count
        for p in range(1, count):
            dist[p] = self._combined_distance(colors, edges, p)

        # 2. 硬切候选 + 闪光抑制。
        hard = {p for p in range(1, count) if dist[p] >= self.config.cut_threshold}
        suppressed = self._suppress_flashes(colors, edges, hard, count)
        hard -= suppressed

        # 3. 渐变趋势归并。
        gradual = self._detect_gradual(colors, edges, dist, timestamps, hard | suppressed)

        boundaries: List[ShotBoundary] = []
        for p in sorted(hard):
            boundaries.append(ShotBoundary(numbers[p], timestamps[p], "hard_cut", dist[p]))
        for p, strength in gradual:
            boundaries.append(
                ShotBoundary(numbers[p], timestamps[p], "gradual", strength)
            )
        boundaries.sort(key=lambda b: b.frame)

        # 4. 最短时长过滤。
        boundaries = self._filter_short_shots(boundaries, timestamps)

        return ShotIndex(
            shots=self._build_shots(boundaries, timestamps, numbers),
            boundaries=boundaries,
        )

    # ---- 内部步骤 ----------------------------------------------------

    def _normalized(self, frames):
        """把特征归一化到 [0, 1]：最大值 > 1.5 时按 0-255 处理。

        颜色与边缘统计分别归一化，避免 0-255 的颜色把 0-1 的边缘
        统计压成噪声。
        """
        color_peak = 0.0
        edge_peak = 0.0
        for f in frames:
            color_peak = max(color_peak, max(f.color, default=0.0))
            if f.edge is not None:
                edge_peak = max(edge_peak, f.edge)
        color_scale = 255.0 if color_peak > 1.5 else 1.0
        edge_scale = 255.0 if edge_peak > 1.5 else 1.0
        colors = [[c / color_scale for c in f.color] for f in frames]
        edges = [None if f.edge is None else f.edge / edge_scale for f in frames]
        return colors, edges

    def _combined_distance(self, colors, edges, p: int) -> float:
        color_dist = _mean_abs_diff(colors[p - 1], colors[p])
        prev_edge, cur_edge = edges[p - 1], edges[p]
        if prev_edge is None or cur_edge is None:
            return color_dist
        w = self.config.edge_weight
        return (1.0 - w) * color_dist + w * abs(prev_edge - cur_edge)

    def _frame_distance(self, colors, edges, i: int, j: int) -> float:
        color_dist = _mean_abs_diff(colors[i], colors[j])
        ei, ej = edges[i], edges[j]
        if ei is None or ej is None:
            return color_dist
        w = self.config.edge_weight
        return (1.0 - w) * color_dist + w * abs(ei - ej)

    def _suppress_flashes(self, colors, edges, hard, count) -> set:
        """识别“内容离开又回来”的孤立尖峰，返回被抑制的边界集合。"""
        cfg = self.config
        suppressed = set()
        for p in sorted(hard):
            if p in suppressed:
                continue
            for length in range(1, cfg.max_flash_frames + 1):
                q = p + length  # 闪光后的第一帧
                if q >= count:
                    break
                if self._frame_distance(colors, edges, p - 1, q) >= (
                    cfg.flash_recovery_threshold
                ):
                    continue
                # 中间帧必须都与闪光前内容明显不同，确认是“插入”的闪光。
                if all(
                    self._frame_distance(colors, edges, p - 1, j)
                    >= cfg.cut_threshold * 0.5
                    for j in range(p, q)
                ):
                    suppressed.update(range(p, q + 1))
                    break
        return suppressed

    def _detect_gradual(self, colors, edges, dist, timestamps, excluded):
        """把连续超软阈值、持续足够久且首尾内容变化足够大的区间
        归并为一个渐变边界，返回 [(边界位置, 强度)]。"""
        cfg = self.config
        count = len(timestamps)
        boundaries = []
        p = 1
        while p < count:
            if p in excluded or dist[p] < cfg.gradual_threshold:
                p += 1
                continue
            start = p
            while p < count and p not in excluded and dist[p] >= cfg.gradual_threshold:
                p += 1
            end = p - 1  # 区间内最后一个软边界
            duration = timestamps[end] - timestamps[start - 1]
            strength = self._frame_distance(colors, edges, start - 1, end)
            if duration >= cfg.gradual_min_duration and strength >= cfg.cut_threshold:
                boundaries.append(((start + end) // 2, strength))
        return boundaries

    def _filter_short_shots(
        self, boundaries: List[ShotBoundary], timestamps
    ) -> List[ShotBoundary]:
        """反复移除导致过短镜头的最弱边界，直到所有镜头满足最短时长。"""
        cfg = self.config
        boundaries = list(boundaries)
        total = timestamps[-1]
        while boundaries:
            starts = [timestamps[0]] + [b.timestamp for b in boundaries]
            ends = [b.timestamp for b in boundaries] + [total]
            durations = [e - s for s, e in zip(starts, ends)]
            # 注意：镜头时长用“下一边界时间 - 本镜头首帧时间”衡量，
            # 与可变帧率时间戳一致。
            worst = None
            for i, b in enumerate(boundaries):
                if min(durations[i], durations[i + 1]) < cfg.min_shot_duration:
                    if worst is None or b.strength < boundaries[worst].strength:
                        worst = i
            if worst is None:
                break
            boundaries.pop(worst)
        return boundaries

    def _build_shots(self, boundaries, timestamps, numbers) -> List[Shot]:
        count = len(timestamps)
        # 边界按帧号映射回排序后的位置。
        pos_by_number = {n: i for i, n in enumerate(numbers)}
        cut_positions = sorted(pos_by_number[b.frame] for b in boundaries)
        shots = []
        starts = [0] + cut_positions
        ends = [p - 1 for p in cut_positions] + [count - 1]
        for i, (s, e) in enumerate(zip(starts, ends)):
            shots.append(
                Shot(
                    index=i,
                    start_frame=numbers[s],
                    end_frame=numbers[e],
                    start_time=timestamps[s],
                    end_time=timestamps[e],
                )
            )
        return shots
