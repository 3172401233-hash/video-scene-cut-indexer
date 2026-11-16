# video-scene-cut-indexer

Python 游戏录屏镜头切换索引库。输入逐帧缩略颜色特征（可选边缘统计）、
帧时间戳和可变帧率信息，输出镜头区间、帧号和时间码。针对游戏录屏
常见的闪光弹白帧、两帧黑帧、淡入淡出和快速运动做了专门过滤。

## 用法

```python
from scene_indexer import FrameFeature, IndexerConfig, ShotIndexer

frames = [
    FrameFeature(index=0, timestamp=0.000, color=[250.0, 10.0, 8.0], edge=0.31),
    FrameFeature(index=1, timestamp=0.033, color=[248.0, 12.0, 9.0], edge=0.30),
    # ... 每帧一条，timestamp 为秒，支持可变帧率
]

result = ShotIndexer(IndexerConfig()).index(frames)
for shot in result.shots:
    print(shot.start_frame, shot.end_frame,
          shot.start_timecode, shot.end_timecode)
for boundary in result.boundaries:
    print(boundary.frame, boundary.kind, boundary.timecode)  # hard_cut / gradual
```

运行测试：

```bash
python -m unittest
```

## 检测流程

1. **候选生成**：计算相邻帧颜色特征的归一化均值绝对差（提供边缘统计时
   按 `edge_weight` 加权融合），超过 `cut_threshold` 的成为硬切候选。
2. **闪光抑制**：若候选边界之后 1~`max_flash_frames` 帧内内容恢复到与
   闪光前一致（距离低于 `flash_recovery_threshold`），整段视为闪光帧
   （闪光弹、两帧闪黑）剔除，不产生边界。
3. **渐变趋势归并**：连续超过 `gradual_threshold` 的区间，若持续时间
   （按时间戳计算，兼容 VFR）达到 `gradual_min_duration` 且区间首尾
   内容差异达到 `cut_threshold`，归并为**一个**渐变边界（取区间中点），
   避免慢速淡入淡出被拆成多段。
4. **最短时长过滤**：短于 `min_shot_duration` 的镜头并入相邻镜头，
   合并时优先保留强度更大的边界。

## 阈值假设

所有距离均为归一化到 [0, 1] 的均值绝对差；特征取值 0-1 或 0-255
均可（自动归一化，颜色与边缘分别处理）。默认值假设：

| 参数 | 默认 | 假设 |
| --- | --- | --- |
| `cut_threshold` | 0.45 | 硬切前后画面内容差异明显（如红场景切蓝场景约 0.67） |
| `gradual_threshold` | 0.015 | 淡入淡出每帧变化均匀，15 帧以上的渐变每帧约 0.02 |
| `gradual_min_duration` | 0.4s | 真实渐变转场至少持续 0.4 秒；快速运动扰动更短 |
| `flash_recovery_threshold` | 0.15 | 闪光前后是同一连续画面，差异小于 0.15 |
| `max_flash_frames` | 2 | 闪光帧不超过 2 帧（覆盖单帧闪白、两帧闪黑） |
| `min_shot_duration` | 0.5s | 有效镜头至少 0.5 秒 |
| `edge_weight` | 0.3 | 边缘统计为辅助信号，权重低于颜色 |

游戏画面整体偏暗或 UI 大面积变化时，建议按素材抽样标定后调整
`cut_threshold` 与 `gradual_threshold`。

## 无法识别 / 可能误判的转场类型

- **划像（wipe）、翻页、变形（morph）等空间渐变转场**：画面局部逐块
  替换，若每帧整体颜色差低于 `gradual_threshold` 会被漏检；若变化
  剧烈则退化为硬切，边界位置有数帧误差。
- **超过 `max_flash_frames` 的长闪光/白场转场**（如 5 帧白场）：会被
  当成两个真实镜头，需调大 `max_flash_frames`。
- **场景内全局光照渐变**（日夜循环、技能全屏光效持续数秒）：满足
  渐变趋势条件，可能被误判为渐变转场。
- **切出又切回的快速来回切换**（2 帧内内容恢复）：与闪光帧特征
  一致，会被闪光抑制误删。
- **淡入淡出叠加剧烈运动**：渐变趋势假设变化近似单调，运动干扰
  可能使边界位置偏移或区间被拆断。
- **极低帧率（< 5fps）下的渐变**：单帧变化可能直接超过
  `cut_threshold`，退化为硬切或闪光，渐变归并不再适用。
