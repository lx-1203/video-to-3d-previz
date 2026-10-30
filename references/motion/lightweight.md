# 可选 MediaPipe CPU 动作草稿

仅在用户选择轻量 CPU 草稿时使用；这是独立可选路线，不是 GEM-X 推理，也不是它失败后的自动替代。需单人、清晰可见、恒定帧率的视频。脚本只取一个人体，未实现人物身份跟踪，不能稳定分离多人。官方模型卡也把多人画面列为适用范围之外。[官方模型卡](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20BlazePose%20GHUM%203D.pdf)

官方世界关键点以髋部中点为原点；本工具输出原地、髋部相对的 BVH，未恢复人物在场景中的世界位移。脚接触、手指、精确扭转及场景关系仍需处理。[官方输出说明](https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker/python)

## 独立部署

按主技能清单复制运行文件，保留 `scripts/motion/mediapipe_motion.py` 与同目录 `export_gem_bvh.py`。以下 `<工具目录>`、`<skill>`、视频与输出路径均须替换为本机真实路径，运行环境和输出均放技能目录外。Windows x64 可用独立 Python 环境安装，不要继承不明全局包；干净新电脑完整安装尚未实测。官方支持 Windows，安装入口见[Python 部署说明](https://developers.google.com/edge/mediapipe/solutions/setup_python)，固定包见 [MediaPipe 1.2.0](https://pypi.org/project/mediapipe/1.2.0/)。

```powershell
python -m venv "<工具目录>/runtime/mediapipe-venv"
& "<工具目录>/runtime/mediapipe-venv/Scripts/python.exe" -m pip install "mediapipe==1.2.0" "absl-py==2.3.1" "scipy==1.18.0"
```

pip 同时解析 NumPy、OpenCV 等依赖；安装失败先处理实际下载或依赖错误，不要把已有包路径当成安装通过。该路线不需要 GPU 库、模型 API 或密钥。

独立下载官方 Pose Landmarker Lite 模型（约 6MB，**5,777,746 字节**），不从复制包假定权重已存在：

```powershell
New-Item -ItemType Directory -Path "<工具目录>/runtime/mediapipe-models" -Force
Invoke-WebRequest -Uri "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task" -OutFile "<工具目录>/runtime/mediapipe-models/pose_landmarker_lite.task"
Get-FileHash -LiteralPath "<工具目录>/runtime/mediapipe-models/pose_landmarker_lite.task" -Algorithm SHA256
```

预期 SHA-256：`59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a`。这是本工具核对的模型版本；官方 `latest` 将来可能变化，哈希不符时先停止并核对版本，不能静默换模型。模型与框架许可按官方资料保留，模型卡标注 Apache-2.0。

## 提取与接入

普通可见全身草稿：

```powershell
& "<工具目录>/runtime/mediapipe-venv/Scripts/python.exe" "<skill>/scripts/motion/mediapipe_motion.py" --video "<单人清晰原视频>" --start 0 --duration 5 --output "<新草稿目录>/motion.bvh" --model "<工具目录>/runtime/mediapipe-models/pose_landmarker_lite.task"
```

`--start`、`--duration` 单位秒，可用小数。输出必须是新路径，不覆盖旧 BVH。脚本先输出进度，末尾输出结果 JSON；须检查退出码与最终 `ok`，不能把进度当成功。质量门禁失败保留 `.landmarks.npz`、叠加图和 `.bvh.failure.json`，不生成成功 BVH；不得改名旧文件或降低标准冒充通过。

只需上半身且用户接受范围时，加 `--coverage upper_body`。这时双腿、脚和脚趾保持绑定姿态占位，不声称提取了下肢。`full_body` 的遮挡导致质量门禁失败时，不会自动恢复腿部动作或自动降级。置信度达标也不证明姿态或人物选对，必须检查叠加图与当前候选工程。

BVH 和同名 `.bvh.json` 一起保留。成功导出后，用实际来源开始秒与实际动作时长创建**新任务**：

```powershell
python "<skill>/scripts/motion/motion_workbench.py" init --job "<新任务目录>" --video "<原视频>" --template "<私有blend>" --profile "<匹配映射json>" --config "<本机config>" --start "<来源开始秒>" --duration "<实际时长秒>" --motion "<新草稿目录>/motion.bvh"
```

按 `next_action.command` 执行 `attach-motion`，再 `status` / `retarget`；保留 `imported_motion` 与 `estimator=mediapipe_pose` 来源，不写成 GEM 产物。同名 sidecar 中出现 SOMA 格式标签只表示桥接格式，不表示用了 SOMA/GEM 算法。

技术接入后仍是待核验草稿。按下一步运行 `preview`，取得当前登记的整段原声对照和 `sequence_preview_scope`；原 `preview_scope` 仍只覆盖首帧，单张图 review 不能验收整段。实际看过的范围才可记录结论；看不懂图时请人工核验并保留 unverified。失败保留 `needs_revision`，不能声称自动可交付。后续场景制作、修订和交付回到本次已加载的[主工作流程](../workflow.md)，不递归调用；下一次新调用仍须在线门禁。不擅改任何原片场景或旧交付。DeepSeek 实际执行未验证。
