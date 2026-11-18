# 镜头焦距判定（camera solve）—— 交付与插件

> 由 **杨治新** 提供。解决一个问题：**主体在画面里大小不变时，是「短焦+近」还是「长焦+远」？**
> 两者成像一样大，只能靠**绝对焦距**分辨。判错了，进入画面的近处道具深度会错，转模型就崩。

本项目 / 本目录与 `video-to-3d-previz` 技能包**完全独立**：
不修改 `SKILL.md`、`scripts/`、`references/`、`release-manifest.json`，
所以**技能包的调用前更新门禁不受影响**。

---

## 一、30 秒跑起来（只想看结果就做这三步）

1. 装插件：把 `addon/` 整个文件夹拷到
   `%APPDATA%\Blender Foundation\Blender\<版本>\scripts\addons\camera_solve_addon\`
   （或 Preferences → Add-ons → Install 选 `addon/__init__.py`）
2. 解压 `handover/camera_solve_2026-11-09.zip` 到任意目录
3. Blender → N 面板 → **CameraSolve** → 目录选解压出来的 `camera_solve/`
   → 点 **读取**，再点 **接口自检**

预期看到：

```
[CameraSolve] 自检：可写 7 / 冲突 0 / 保留 AI 0 / 无测量 47｜相对曲线 1 个，其中有基准 1
```

详细步骤和"四种结果各代表什么"见 [`VERIFY.md`](VERIFY.md)。

## 二、结论是什么

**54 个镜头 / 10 个文件**：

| 判定 | 镜头数 |
|---|---|
| 情况1（短焦+近） | **0** |
| 情况2（长焦+远） | **6** |
| 两者之间（30–45°） | 1 |
| 无法判定（给了相对倍率） | 3 |
| 无法判定（画面无可用信息等） | 44 |

逐镜头明细见 [`docs/20-情况1还是情况2-逐镜头判定表.md`](docs/20-情况1还是情况2-逐镜头判定表.md)。

> **一个对你们流程有用的结论**：已测出的镜头**没有一个是情况1**，全是长焦远拍。
> 所以"道具进镜头就毁效果"的主因大概率是**焦距填错**（长焦被当成广角），
> 而不是取景问题 —— 把表里的 f 填进去，这类错误会直接消失。

## 三、插件做了什么（三条设计原则）

| 你们的要求 | 实现 |
|---|---|
| 机位是 AI 定的，要支持人工可控关键帧 | 插件**只写 `lens` 关键帧**，`cam_loc` / `rotation` 一个字节都不碰；没有测量结果的镜头**一个关键帧都不写** |
| 焦距口径（毫米/像素）、sensor 宽要算法自动匹配 | 见下 —— 不需要问人 |
| 直接不用 AI，让算法自动化 | 只认**物理方法**测出的 `f_px`；学习模型的值放 `prior_f_px`，**只显示、绝不写入** |

### 口径为什么不需要问人

能从画面测出来的只有 **`f_px`**。Blender 的 `lens(mm)` 与 `sensor_width(mm)` 只以**比值**出现：

```
lens = f_px × sensor_width / width_px
```

所以 sensor 宽度在数学上**无法从画面反推**。但只要 lens 与 sensor **成对**设置，
**渲染视角完全相同**（实测 4 种约定 vfov 都是 25.4516°）。
⇒ 插件用你自己工程里的 `sensor_width` 反算 lens，视角永远对。

## 四、与你们的 AI 如何共存（方案 A）

同一个镜头上会有两个 f：你们 AI 的和我测的。插件里可切：

| `f 优先级` | 行为 |
|---|---|
| **算法优先**（默认） | 有物理测量就覆盖 |
| **AI 优先** | 相机上已有 `lens` 关键帧就不动 |
| **报警** | 差超过阈值不写、只报，人工裁决 |

**没有绝对 f 的镜头也能用**：勾上「AI 的 f × 相对曲线」，
逐帧 `lens(t) = 基准 lens × ratio(t)`。
基准 lens 的优先级：**手填 `锚点 mm` > AI 的 lens 曲线 > 相机当前 lens（非默认 50mm）**。

## 五、目录

```
camera-solve/
  README.md                  ← 本文
  VERIFY.md                  ← 自检 4 步 + 四种结果的含义
  addon/                     ← Blender 插件（jsonio.py 纯逻辑 + __init__.py 界面）
  handover/                  ← 交付包（camera_solve/v1 JSON）+ 摘要
  docs/20-...判定表.md        ← 逐镜头中文判定表
```

## 六、数据可追溯

- `handover/summary.csv`：54 行逐镜头摘要
- `handover/camera_solve/*.json`：`camera_solve/v1`，含逐帧 `f_px` / `vfov_deg` / `f_curve`
- 每个判定都带 `evidence_grade`、`confidence`、`regime_reason`
- 导出前过三道闸（段内硬切 / 段内 f 漂移 / 学习模型不混入 `f_px`），
  并通过独立校验器（重新解码复核硬切）**0 错 0 警**
