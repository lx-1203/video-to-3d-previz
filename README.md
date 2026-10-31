# 视频转3D白模

将参考视频复刻为可编辑的Blender白模预演，覆盖空间、机位、人物动作、环境还色、身份色、球面焦点标记、头身与手型、原声音轨与4K上下对照交付。包含实际制作经验、质量门槛和任务模板；技术检查通过不等于整体95%视觉相似度通过。已要求场景还色时，“白模”不能解释为灰白环境。

## 使用

调用名：`$video-to-3d-previz`。技能正文在[SKILL.md](SKILL.md)，公开版仅包含通用方法和匿名案例，不包含原视频、Blender工程、个人路径或会话记录。

已合并原视频动作工作台：从本入口按[内置动作工作流](references/motion-workflow.md)调用 `scripts/motion/motion_workbench.py`，完成视频动作/BVH、指定模型接入和整段原声检查对照。无需另装同名companion技能；后端运行环境、权重、Blender/FFmpeg及私有模板/profile仍单独配置。动作检查后继续原有场景、接触和正式交付流程。

需要复制到其他 Windows＋NVIDIA 电脑时，使用[接单工具与便携安装](references/order-toolkit.md)的构建器与安装器。私有模板和真实插件 ZIP 在本机加入私有包；公共仓库仅发布工具。安装器重建本机路径，不携带旧环境、权重、任务或客户影片。

1.5.0增加[多人候选接口](references/multi-actor.md)：保留全检测与轨迹、少量身份锚/假设展开、官方多实例Pose受控调用与缓存绑定，以及显式profile的模板臂方向采样桥。它们是受限制作工具；不承诺自动识别人名、恢复多人共同世界深度、连续动作或正确接触。真实短例验证与每片正式骨架/交付审核分开。

安装到Codex技能目录的`video-to-3d-previz`文件夹。复制`release-manifest.json`以及其中列出的运行文件，勿把整个Git仓库（含.git、README和tests）当成安装目录。然后在本机生效的AGENTS.md写入[调用前更新规则](references/updates.md)。

每次调用前运行：

```text
python <安装目录>/video-to-3d-previz/scripts/check_update.py
```

只有退出码0且status为current或updated、rerun_required=false，才可重新读取技能继续。网络故障、本地冲突、损坏文件或旧版本均阻止使用；没有离线回退或跳过开关。更新器本身变化时必须再检查一次。

## 维护验证

Python 3.10+与Git；更新门禁和覆盖账本只用标准库。内容扫描工具及其真实媒体回归测试另需PyAV、NumPy、Pillow（`python -m pip install av numpy Pillow`），使用PyAV编解码，不依赖固定FFmpeg可执行文件路径。远端检查使用git ls-remote，无需GitHub令牌或匿名REST API配额。在维护仓库中：

完整动作回归还需已配置的GEM/SOMA后端环境及PyTorch、SciPy。运行完整测试时，将下方 `python` 换为该环境的解释器，并把 `FFMPEG_EXE` 设为本机FFmpeg的完整路径；缺少该路径时真实音轨边界用例会跳过，跳过不能计作通过。动作状态CLI本身仍只依赖标准库，使用者按[动作工作流](references/motion-workflow.md)配置所需阶段。

```text
python -m unittest discover -s tests -v
python scripts/build_manifest.py
```

随后按本机skill-creator提供的quick_validate.py校验技能，审查差异，提交并推送main。清单覆盖所有运行文件的SHA-256；更新从固定commit下载，备份完成后切换，失败则阻止调用。

这不是自动视频重建软件，也不保证任意输入达到95%。调用前约束由AGENTS.md、技能入口和更新脚本共同实现，不是宿主权限隔离机制。
