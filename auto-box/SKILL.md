---
name: auto-box
description: 视频转 3D 白模流程中的「套框 → 身份 → 骨架」自动化线路。当需要从一个视频得到稳定的多人 2D 骨架时使用：ONNX 检测出框并去重，程序用几何规则筛可疑帧，视觉模型或人判断框与身份，程序把判断落成身份锚并只重跑受影响的帧。适用于多人遮挡、换号、框过长、漏框等干扰问题；不负责 3D 重建与 Blender 渲染。
metadata:
  version: "0.1.0"
  requires: python3, ffmpeg, Pillow, torch, GEM-X 权重, onnxruntime
---

# auto-box

## 何时用

- 一段视频里有多个人，检测框互相干扰、换号、过长、漏框
- 需要一个可复现的“框 → 身份 → 骨架”结果，且改一次只重跑受影响的部分
- 想让人或视觉模型参与判断，但不让它直接写像素坐标

## 核心约定

> **模型只判断，程序只执行。**

视觉模型输出的是相对操作（`shrink_bottom` / `drop` / `same_as` / `mark_missing` / `assign_actor`），
程序按实测框尺寸换算。所有修正带 `evidence` 与影响面记录。

## 流程

```
① 检测（ONNX + 去重 + 外观追踪）
      ↓  frames.jsonl
② 筛可疑帧（几何规则，秒级）
      ↓  复核图
③ 判断（视觉模型或人）
      ↓  decisions.json
④ 落成锚（anchor_edit / apply_decisions）
      ↓  anchors.json
⑤ 首跑（run_shot）
      ↓  pose.pt → filled.json → 骨架图
⑥ 发现错误 → 修正闭环（correct.py）
```

## 关键脚本

| 脚本 | 职责 | 何时用 |
|---|---|---|
| `preflight.py` | 环境自检（包、ffmpeg、路径、权重、写权限） | 开工前一次 |
| `multi_actor_detect_t4.py` | 检测 + 去重 + 外观追踪 | 每镜一次 |
| `review_candidates.py` | 找可疑帧、渲染对照图 | 每镜一次 |
| `apply_decisions.py` | 模型判断 → 框覆盖 + 锚 | 有模型输出时 |
| `anchor_edit.py` | 结构化改锚 | 手工修正时 |
| `run_shot.py` | 首跑全链路 | 每镜一次 |
| `correct.py` | 修正闭环（diff → patch → refill → render） | 发现错误时 |
| `diff_anchors.py` | 旧新锚对比 → 受影响帧 | correct 内部调用 |
| `pose_patch.py` | 只补跑受影响帧并合并 | correct 内部调用 |
| `fill_pose_gaps.py` | 缺失帧插值/保持 | 每次 pose 后 |
| `render_skeletons.py` | 2D 骨架，区分实测/预测 | 每次都要看 |

## 顺序与依赖

0. 先跑 `preflight.py`：配置、权重、官方脚本任一缺失都会在长任务开始前被拦下。
1. 必须**先有检测**才能筛帧；`review_candidates` 不跑模型。
2. 必须**先有锚**才能出候选；锚是身份的唯一定义处。
3. `correct.py` 必须**已有一次完整 pose 结果**，它只做增量。

## 硬规则

1. 同一 track 同一帧只能属于一个演员（`anchor_edit.py` 会拒绝冲突）。
2. `filled` / `held` 不是观测，导出时必须保留 `source` 字段。
3. 视觉模型的判断不得写成 `confirmed`，除非有人确认过。
4. 内参未标定（默认 fx=1000）时，3D 尺度不可用于测量。

## 典型错误与处理

| 现象 | 原因 | 处理 |
|---|---|---|
| 同一人两个框 | 检测器把人和前景一起框 | 检测层去重（包含率 ≥0.90） |
| 框过长 | 人下半身被遮挡或前景吞并 | `shrink_bottom` 或检测层收边 |
| 骨架落在别人身上 | 锚区间错 | `anchor_edit` 改区间 → `correct.py` |
| 骨架突然消失 | 该帧无框 | `fill_pose_gaps` 插值或保持 |
| 两人交叉后换号 | 轨迹交叉 | 锚区间拆分 + `correct.py` |

## 验证方式

每次改动后看三件事：

1. `diff_anchors` 报的受影响帧数是否符合预期（改 4 帧就该是 4）
2. 骨架图上 `source` 标记是否正确（实测实线、预测半透明）
3. 缺失帧数是否为 0（除非确实看不见人）
