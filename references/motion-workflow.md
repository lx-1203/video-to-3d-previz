# 视频动作识别与指定模型接入

这是本主技能的内置模块，原 `video-motion-workbench` 工具已随包携带；不要转去调用另一个技能。主入口的在线更新门禁统一覆盖这些文件，本次调用通过后按需使用；下一次新调用仍须重新联网检查。

## 先选择当前阶段

- 新视频需要动作底稿：先读[操作协议](motion/protocol.md)和[订单衔接](motion/order-workflow.md)，按镜头选目标连续可见的短段（默认先5秒），执行固定命令，再继续本主流程的场景、机位、手部/道具接触及正式交付。
- 已有动作任务：从实际 `<job>` 运行 `status`，沿返回的下一步恢复；有效动作和预览可复用，不重复下载或识别。
- 仅修改已有场景、机位、接触或导出：保留已修工程，按主流程对应阶段继续，不强制重新识别。
- 用户明确指定外部BVH：按协议以 `init --motion` 和 `attach-motion` 保留真实外部来源；不冒充视频推理结果。

## 固定入口

`<skill>` 指本主技能根目录；命令中的路径须替换为本机真实路径。客户端只需读写文件和运行本地命令；不需要 DeepSeek API、密钥或新MCP。实际DeepSeek客户端执行与新电脑部署仍未验证。

```powershell
python "<skill>/scripts/motion/motion_workbench.py" doctor --config "<本机config.local.json>"
python "<skill>/scripts/motion/motion_workbench.py" init --job "<新任务目录>" --video "<原片>" --template "<私有模板.blend>" --profile "<匹配的私有映射.json>" --config "<本机config.local.json>" --start 0 --duration 5
python "<skill>/scripts/motion/motion_workbench.py" status --job "<任务目录>"
```

正常视频路线是 `extract → retarget → preview → 按实际证据review`。每步检查退出码和单个JSON；`next_action.command` 是逐项传参的argv数组，`null` 时读原因，不拼eval。`doctor`只查路径，不证明依赖、权重、推理或画面通过。协议给出完整命令和失败恢复方式；不让模型临时重写骨骼数学或编码脚本。

## 运行与验收边界

主包包含脚本和协议，不含GEM运行环境/权重、Blender、FFmpeg、客户模板或客户映射；本机配置和任务放技能目录外。模板与映射缺失时报告具体缺项，不能拿通用人物代替。按需部署先查看 `scripts/motion/setup_backend.py --help` 和 `--plan-only`；不会因读取本技能而自动下载约7GB权重。上游许可见[使用与许可说明](motion/notices.md)。CPU MediaPipe仅在用户明确选择时按[轻量路线](motion/lightweight.md)执行，不自动替代GEM失败。

GEM当前只取一个主要人物，默认按累计检测框面积选择，不能显式锁定任意演员或自动恢复多人。绿框表示有框观测，橙框是失跟踪后的补齐，人物可能仍在画内；无跟踪证据保留unknown。遮挡下的腿脚、世界位移及人物接触须实际核验。

要判断是否提高准确度，按[同帧骨架与姿态透视叠加](character-and-motion.md#同帧骨架与姿态透视叠加)比较同PTS、同机位的实际模型投影；独立检查机位的上下预览不能直接用于像素误差排名。检查骨架变化是否真正传到可见胸、肩、头等部件，骨骼有曲线但胸块未动或肩胸开缝仍属待修。观察到的二维位置与动作时序可作软参考，遮挡关节和未经校准的根位移不当作真值；有观测框、命令退出0或成功记录failed，都不能证明准确度提升。

检查预览采用原片在上、检查模型在下并保留该段原声；检查副本的机位和显示色不替代订单正式机位/身份色。首帧PNG与整段影片的核验范围分别登记；生成影片不等于看过，不能看图的模型保留unverified并提供真实文件给人工。技术接入和动作review不表示整片或95%通过。

本次主流程内完成动作候选后，回到[空间与动作搭建](workflow.md#4-空间与动作分层搭建)及[预览与迭代](workflow.md#5-预览与迭代)，不递归调用本技能。正式原生4K、场景/接触复核、音轨和最终文件验收仍按主流程；短段动作检查影片不冒充正式成片。
