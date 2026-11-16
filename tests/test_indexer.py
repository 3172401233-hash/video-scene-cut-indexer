"""镜头索引器的边界与误检测试。

运行方式：python -m unittest
"""

import unittest

from scene_indexer import (
    FrameFeature,
    IndexerConfig,
    ShotIndexer,
    format_timecode,
)

FPS = 30.0
RED = [255.0, 0.0, 0.0]
BLUE = [0.0, 0.0, 255.0]
GREEN = [0.0, 255.0, 0.0]
BLACK = [0.0, 0.0, 0.0]
WHITE = [255.0, 255.0, 255.0]


def make_frames(colors, timestamps=None, edges=None):
    """按颜色序列构造 FrameFeature 列表，默认 30fps 恒定帧率。"""
    if timestamps is None:
        timestamps = [i / FPS for i in range(len(colors))]
    frames = []
    for i, (color, ts) in enumerate(zip(colors, timestamps)):
        edge = edges[i] if edges is not None else None
        frames.append(FrameFeature(index=i, timestamp=ts, color=color, edge=edge))
    return frames


def solid(color, count):
    return [list(color) for _ in range(count)]


def lerp(a, b, t):
    return [x + (y - x) * t for x, y in zip(a, b)]


class HardCutTest(unittest.TestCase):
    def test_hard_cut_boundary_frame_and_timecode(self):
        colors = solid(RED, 30) + solid(BLUE, 30)
        result = ShotIndexer().index(make_frames(colors))

        self.assertEqual(len(result.boundaries), 1)
        boundary = result.boundaries[0]
        self.assertEqual(boundary.kind, "hard_cut")
        self.assertEqual(boundary.frame, 30)
        self.assertEqual(boundary.timecode, "00:00:01.000")

        self.assertEqual(len(result.shots), 2)
        first, second = result.shots
        self.assertEqual((first.start_frame, first.end_frame), (0, 29))
        self.assertEqual((second.start_frame, second.end_frame), (30, 59))
        self.assertEqual(first.start_timecode, "00:00:00.000")
        self.assertEqual(second.start_timecode, "00:00:01.000")

    def test_multiple_hard_cuts(self):
        colors = solid(RED, 40) + solid(BLUE, 40) + solid(GREEN, 40)
        result = ShotIndexer().index(make_frames(colors))
        self.assertEqual([b.frame for b in result.boundaries], [40, 80])
        self.assertEqual(len(result.shots), 3)


class FlashSuppressionTest(unittest.TestCase):
    def test_single_white_flash_is_not_a_cut(self):
        colors = solid(RED, 20) + solid(WHITE, 1) + solid(RED, 20)
        result = ShotIndexer().index(make_frames(colors))
        self.assertEqual(result.boundaries, [])
        self.assertEqual(len(result.shots), 1)
        self.assertEqual((result.shots[0].start_frame, result.shots[0].end_frame), (0, 40))

    def test_two_frame_black_flash_is_not_a_cut(self):
        colors = solid(RED, 20) + solid(BLACK, 2) + solid(RED, 20)
        result = ShotIndexer().index(make_frames(colors))
        self.assertEqual(result.boundaries, [])
        self.assertEqual(len(result.shots), 1)

    def test_flash_then_real_cut(self):
        # 闪光之后紧跟一个真实硬切，硬切不能被误抑制。
        colors = solid(RED, 20) + solid(WHITE, 1) + solid(RED, 20) + solid(BLUE, 30)
        result = ShotIndexer().index(make_frames(colors))
        self.assertEqual(len(result.boundaries), 1)
        self.assertEqual(result.boundaries[0].frame, 41)
        self.assertEqual(result.boundaries[0].kind, "hard_cut")


class GradualTransitionTest(unittest.TestCase):
    def _fade_sequence(self):
        # 30 帧红 -> 15 帧淡出到黑 -> 15 帧淡入到蓝 -> 30 帧蓝。
        colors = solid(RED, 30)
        colors += [lerp(RED, BLACK, t / 15.0) for t in range(1, 16)]
        colors += [lerp(BLACK, BLUE, t / 15.0) for t in range(1, 16)]
        colors += solid(BLUE, 30)
        return colors

    def test_fade_out_fade_in_merges_to_single_boundary(self):
        result = ShotIndexer().index(make_frames(self._fade_sequence()))
        self.assertEqual(len(result.boundaries), 1)
        boundary = result.boundaries[0]
        self.assertEqual(boundary.kind, "gradual")
        # 渐变区间覆盖帧 30..59，边界应落在区间中点附近。
        self.assertTrue(40 <= boundary.frame <= 50, boundary.frame)
        self.assertEqual(len(result.shots), 2)

    def test_fade_timecode_uses_timestamps(self):
        result = ShotIndexer().index(make_frames(self._fade_sequence()))
        boundary = result.boundaries[0]
        expected = format_timecode(boundary.frame / FPS)
        self.assertEqual(boundary.timecode, expected)


class FastMotionTest(unittest.TestCase):
    def test_brief_motion_spike_is_not_a_cut(self):
        # 3 帧中等强度扰动（超过软阈值但远小于硬切阈值），持续太短。
        colors = solid(RED, 30)
        colors += [[200.0, 60.0, 0.0] for _ in range(3)]
        colors += solid(RED, 30)
        result = ShotIndexer().index(make_frames(colors))
        self.assertEqual(result.boundaries, [])
        self.assertEqual(len(result.shots), 1)

    def test_sustained_motion_without_content_change_is_not_gradual(self):
        # 持续 1 秒的来回扰动：每步都超软阈值，但首尾内容基本一致，
        # 不满足渐变趋势，不能归并出边界。
        colors = solid(RED, 10)
        for i in range(30):
            colors.append([235.0, 20.0, 0.0] if i % 2 else list(RED))
        colors += solid(RED, 10)
        result = ShotIndexer().index(make_frames(colors))
        self.assertEqual(result.boundaries, [])
        self.assertEqual(len(result.shots), 1)


class RepeatedFramesTest(unittest.TestCase):
    def test_repeated_frames_single_shot(self):
        result = ShotIndexer().index(make_frames(solid(RED, 60)))
        self.assertEqual(result.boundaries, [])
        self.assertEqual(len(result.shots), 1)
        self.assertEqual((result.shots[0].start_frame, result.shots[0].end_frame), (0, 59))

    def test_repeated_frames_with_variable_frame_rate(self):
        # 可变帧率 + 重复帧：时间戳间隔不均也不能产生边界。
        timestamps = [0.0, 0.5, 0.55, 0.9, 1.4, 1.45, 1.5, 2.0]
        result = ShotIndexer().index(make_frames(solid(BLUE, 8), timestamps))
        self.assertEqual(result.boundaries, [])
        self.assertEqual(len(result.shots), 1)
        self.assertEqual(result.shots[0].end_timecode, "00:00:02.000")


class VariableFrameRateTest(unittest.TestCase):
    def test_min_shot_duration_uses_timestamps(self):
        # VFR 下镜头 B 只有 0.05s，应被合并；若按帧数算则不会。
        colors = solid(RED, 3) + solid(BLUE, 2) + solid(BLACK, 3)
        timestamps = [0.0, 0.4, 0.8, 0.85, 0.9, 1.5, 2.0, 2.5]
        result = ShotIndexer().index(make_frames(colors, timestamps))
        self.assertEqual(len(result.shots), 2)
        for shot in result.shots:
            self.assertGreaterEqual(shot.duration, 0.0)

    def test_boundary_timecode_reflects_vfr_timestamps(self):
        colors = solid(RED, 2) + solid(BLUE, 3)
        timestamps = [0.0, 0.1, 0.5, 0.9, 1.0]
        result = ShotIndexer().index(make_frames(colors, timestamps))
        self.assertEqual(len(result.boundaries), 1)
        self.assertEqual(result.boundaries[0].frame, 2)
        self.assertEqual(result.boundaries[0].timecode, "00:00:00.500")


class EdgeStatisticsTest(unittest.TestCase):
    def test_edge_change_contributes_to_distance(self):
        # 颜色不变但边缘统计突变：提高边缘权重后应检出硬切。
        config = IndexerConfig(edge_weight=0.5)
        colors = solid(RED, 60)
        edges = [0.1] * 30 + [1.0] * 30
        result = ShotIndexer(config).index(make_frames(colors, edges=edges))
        self.assertEqual(len(result.boundaries), 1)
        self.assertEqual(result.boundaries[0].frame, 30)


class TimecodeTest(unittest.TestCase):
    def test_format(self):
        self.assertEqual(format_timecode(0.0), "00:00:00.000")
        self.assertEqual(format_timecode(3661.5), "01:01:01.500")
        self.assertEqual(format_timecode(59.9994), "00:00:59.999")

    def test_negative_rejected(self):
        with self.assertRaises(ValueError):
            format_timecode(-0.1)


class InputValidationTest(unittest.TestCase):
    def test_empty_input(self):
        with self.assertRaises(ValueError):
            ShotIndexer().index([])

    def test_unsorted_input_is_sorted(self):
        frames = make_frames(solid(RED, 30) + solid(BLUE, 30))
        frames.reverse()
        result = ShotIndexer().index(frames)
        self.assertEqual(len(result.boundaries), 1)
        self.assertEqual(result.boundaries[0].frame, 30)


if __name__ == "__main__":
    unittest.main()
