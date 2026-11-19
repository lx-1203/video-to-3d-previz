# auto-box · 视频 → 多人骨架 的自动化线路

输入一段视频，输出**每一帧、每一个人**的 2D 骨架和 3D 关节，并且能让人或视觉模型参与修正“谁是谁”。

它不是为了跑通一次，而是为了**改得动**：改一帧身份，只重跑那一帧，其余复用。

---

## 1. 它解决什么问题

多人视频里，检测器和追踪器会犯四类错：

| 现象 | 原因 |
|---|---|
| 同一个人两个框 | 检测器把人和前方桌子一起框进去 |
| 框过长 | 下半身被遮挡，检测器把前景并进人体 |
| 换号 | 两人交叉，轨迹互相劫持 |
| 漏框 | 遮挡、虚焦、远处太小 |

这些靠调追踪器参数解决不了。本项目的做法是：

> **用几何规则找出可疑帧 → 人或视觉模型看图判断 → 程序把判断落成“身份锚” → 只重跑受影响的帧。**

程序只做确定性的事（改框、算影响面、补跑、合并），判断留给人或模型。

---

## 2. 前置条件

**这个文件夹是自包含的**：权重、官方脚本、示例视频、姿态解释器都在里面（`vendor/` 与 `assets/`），配置里全是相对路径，换位置、改名都不需要改配置。

对方机器上只需要两样东西：

| 依赖 | 说明 |
|---|---|
| Python 3.9+ | 跑流程脚本（需要 cv2 / numpy / scipy / Pillow / torch） |
| ffmpeg | 抽帧，需在 PATH 里 |

`vendor/venv` 是自带的姿态解释器（torch + soma + omegaconf + transformers + open3d），
**不需要联网也不需要系统里装过这些包**。

如果对方的 Python 里没有 torch，用自带的解释器跑流程即可：

```bash
vendor\venv\Scripts\python.exe scripts\preflight.py
```

只有在换到另一台机器、而 `vendor/venv` 因为解释器版本不符而失效时，才需要重建：

```bash
python scripts/setup_venv.py --force      # 需要网络，装到 vendor/venv
```

---

## 3. 五分钟上手

### ① 不需要改配置

`config/paths.json` 里全是相对路径，指向包内的 `vendor/` 和 `assets/`。直接跳到自检：

### ② 自检

```bash
python scripts/preflight.py
```

逐项检查 ffmpeg、配置项、权重、官方脚本、写权限，并**用包内解释器实际导入** cv2 / torch / av / onnxruntime 等依赖。
**全绿再往下走。** 用系统 Python 或 `vendor\venv\Scripts\python.exe` 启动都可以，子进程一律走包内解释器。

### ③ 写镜头表

```json
[{"shot_id": "SH01_doorway", "source_zero_start": 0, "source_zero_end": 226}]
```

存为 `runs/shots_sh01.json`。帧号是**源文件零基**，闭区间。

### ④ 检测

```bash
python scripts/multi_actor_detect_t4.py \
  --source <video> --source-sha256 <sha> \
  --repo <GEM-X> --model <yolox.onnx> --model-sha256 <sha> \
  --shots runs/shots_sh01.json \
  --output-new-dir runs/sh01_detect --threads2
```

产出 `runs/sh01_detect/frames.jsonl`：每帧的框、轨迹、被丢弃/收边的记录。

### ⑤ 找可疑帧

```bash
python scripts/review_candidates.py \
  --detections runs/sh01_detect/frames.jsonl \
  --source <video> --out runs/sh01_review --limit 20
```

产出 `candidates.json` 和一批对照图（左整帧、右目标框放大）。

### ⑥ 写身份锚

看着图，把“哪个 id 是谁、从第几帧到第几帧”写成 JSON：

```json
{
  "actor_ids": ["甲", "乙", "丙"],
  "anchors": [
    {"shot_id": "SH01_doorway", "track_id": 1, "actor_id": "甲",
     "start_frame": 0, "end_frame": 226,
     "confirmation": "confirmed", "evidence": "粉西装男，全程"}
  ]
}
```

同一个人可以在不同区间用不同 id（换号），只要同一 id 在同一帧不同时属于两人。

### ⑦ 首跑

```bash
python scripts/run_shot.py \
  --config config/paths.json --shot SH01_doorway \
  --detections runs/sh01_detect/frames.jsonl \
  --anchors runs/sh01_anchors.json \
  --work runs/sh01_pose \
  --frames 0,40,100,200
```

内部依次：身份提案 → 候选校验 → 姿态推理 → 缺失帧补全 → 出骨架图。

---

## 4. 修正一帧

比如“134 帧乙的骨架落在丙身上了”。

```bash
# ① 结构化改锚
python scripts/anchor_edit.py --anchors old.json --out new.json \
  --set SH01_doorway 丙 2 0 139 \
  --clear SH01_doorway 乙 2 134 137

# ② 修正闭环
python scripts/correct.py --config config/paths.json \
  --detections runs/sh01_detect/frames.jsonl \
  --anchors-old old.json --anchors-new new.json \
  --pose-existing runs/sh01_pose/pose/pose.pt \
  --work runs/sh01_fix --frames 134,140
```

`correct.py` 自动完成：算哪几帧受影响 → 只用这些帧重建候选 → 补跑姿态 → 合并回原结果 → 重新补全 → 重画。

改动帧数越少，补跑越便宜。上面这个例子只有 4 帧，约 70 秒。

---

## 5. 目录

```
auto-box/                       约 8 GB，自包含
├── README.md                    本文件
├── SKILL.md                     给 AI 助手读的技能说明
├── config/paths.json            相对路径，通常不用改
├── assets/
│   ├── source.mp4               示例视频（1280×720，30fps，699 帧）
│   └── intrinsics.json          估计内参
├── vendor/                      外部依赖，全部在包内
│   ├── GEM-X/                   检测与姿态模型权重（7.3 GB）
│   ├── video-to-3d-previz/      官方封装脚本
│   └── venv/                    姿态解释器（自带 torch 等）
├── references/
│   └── joint_parents.json       77 关节层级
├── scripts/
│   ├── preflight.py             环境自检
│   ├── multi_actor_detect_t4.py 检测 + 去重 + 外观追踪
│   ├── appearance_tracker.py    外观门控追踪器
│   ├── review_candidates.py     几何筛可疑帧 + 对照图
│   ├── apply_decisions.py       模型判断 → 框覆盖 + 锚
│   ├── apply_box_overrides.py   框覆盖写回检测流
│   ├── anchor_edit.py           改锚（区间裁剪 + 冲突校验）
│   ├── diff_anchors.py          旧新锚对比 → 受影响帧
│   ├── run_shot.py              单镜首跑全链路
│   ├── pose_patch.py            只补跑受影响帧并合并
│   ├── correct.py               修正闭环
│   ├── filter_tracks.py         只保留指定轨迹（剔除背景路人）
│   ├── build_candidate.py       锚 + 检测 → 姿态候选
│   ├── fill_pose_gaps.py        缺失帧插值 / 短时保持
│   ├── render_skeletons.py      2D 骨架（实测实线 / 预测半透明）
│   └── font_util.py             字体回退
└── runs/
    ├── example_sh04/            样例产物（检测 / 锚 / filled / 三张骨架图）
    └── （其余目录由你自己跑出来）
```

`runs/example_sh04` 是第 4 镜（251 帧、双人中景）的真实产物，可以对照着看
`frames.jsonl` / `anchors.json` / `filled.json` / `skel/*.png` 长什么样。
其余产物（尤其 `pose/pose.pt`，单个 20–100 MB）不进交付包，跑的时候自己生成。

---

## 6. 产物

| 文件 | 内容 |
|---|---|
| `frames.jsonl` | 逐帧检测与轨迹，含 `box_adjustments`（被丢弃/收边的框） |
| `proposal.json` | 身份提案与冲突检查 |
| `candidate.json` | 姿态计划输入（每帧期望哪些演员） |
| `pose/pose.pt` | 逐裁剪推理结果（关节位置、2D 关键点、投影） |
| `filled.json` | 逐帧关节 + `source`（observed / filled / held / missing） |
| `skel/*.png` | 骨架叠加图，图例标注每个演员的来源 |

`filled.json` 的结构：

```json
{
  "actors": {
    "甲": {
      "134": {"k2d": [[x, y], "..."], "joints": [[X, Y, Z], "..."],
               "source": "filled", "bracket": [130, 138], "travel": 0.08}
    }
  },
  "summary": {"甲": {"observed": 222, "filled": 5, "held": 0, "missing": 0}}
}
```

**`source` 不是装饰**：`filled`/`held` 是推断，不是观测。下游把它们当实测用会出错。

---

## 7. 三条硬规则

1. **同一 track 同一帧只能属于一个演员**。`anchor_edit.py` 会拒绝冲突。
2. **`filled`/`held` 必须保留标记**，不能混进观测。
3. **视觉模型的判断不等于人工确认**。`confirmation` 只能是 `hypothesis`，除非有人看过。

---

## 8. 常见问题

**检测很慢？** 约 3 秒/帧（CPU）。251 帧约 14 分钟，正常。

**姿态很慢？** 每人每帧约 1.4 秒。一镜 3 人 250 帧约 15 分钟。

**`candidate.json` 报 `unmapped detections`？** 有检测框没写进锚。要么给它写锚，要么用 `filter_tracks.py` 剔掉。

**`candidate.json` 报 `missing explicit frame mapping`？** `build_candidate.py` 的 `--shot` 和锚里的 `shot_id` 不一致。

**骨架只有头？** 那是特写镜头。GEM 的 body 模型假设裁剪框里是完整人体，特写时 77 个关节只有十几个在画面内。

**两人交叉后换号？** 正常。用 `anchor_edit.py` 拆区间，再 `correct.py`。

---

## 9. 已知限制

- 追踪器在拥挤/交叉场景仍会换号，需要锚兜底
- 特写镜头（只有头肩）的身体姿态不可信
- `fill_pose_gaps.py` 只做线性插值或短时保持，不预测步态
- 内参默认是估计值，3D 尺度不可用于测量
- 没有人群实例分割，贴身站立时框会重叠
- 不产出 BVH / Blender 文件；那是下一段（重定向到角色）
