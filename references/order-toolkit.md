# 接单工具与便携安装

本流程用于制作方内部搬迁工具，不是客户影片交稿包。通用 Skill、私有模型规范、候选骨架映射、Blender 插件分层携带；私有模型、客户视频、工程和机器配置不进入公共技能仓库。

## 动作、场景、镜头与颜色

复用[动作工作流](motion-workflow.md)提取动作、登记目标人物身份和时间映射；动作底稿接入当前模型后，继续[参考分析与机位](reference-and-camera.md)、[造型与动作](character-and-motion.md)和[质量门槛](quality-gates.md)。不要用一次骨架导入替代场景接入和成片验收。

接入现有工程时，先登记当前有效场景、相机、角色位置、身份色及实际渲染引擎。私有客户模板只用于指定客户，保留已修好的场景与动作。骨架命名相同不保证模型轴向、枢轴和臂掌长度相同；profile 是候选映射，必须在实际模型上检查。固定角色身份色跨镜保持，眼条和胸标读取承载表面的有效底色计算对立色或互补色，不能用纯黑或近黑替代。

导出插件应从保存后重开的冻结副本生成帧和成片，绑定源 SHA、相机、帧段、fps、分辨率、原声及依赖。预览与原生 4K 正式输出分开；预览通过不能充当正式输出。最终交稿必须完成原片与白模同帧骨架叠加的真实审核，按[固定证据合同](skeleton-overlay-contract.md)绑定当前工程与视频；插件和最终包入口使用同一个 `scripts/validate_skeleton_overlay.py`，缺失、未验证或失败不得以技术通过替代。原生试渲可用于制作审核证据，仍不是交稿。实际插件名称、版本、入口、支持的 Blender 版本和验证证据以本次提供的真实 ZIP 为准，不从文件名推断可用性。插件若仅导入参考 BVH，不得描述为已自动完成角色绑定或人物接触修复。

## 构建私有搬迁包

`scripts/portable/build_bundle.py` 与同目录 `install_bundle.py` 是自包含标准库工具，需要 Python 3.10+。构建前先通过主 Skill 正常在线更新门禁，使用真实已核验的插件 ZIP；不要以空插件或占位文件冒充集成。

```text
python <skill>/scripts/portable/build_bundle.py --skill <skill> --private-skill <private-client-skill> --profile <candidate-profile.json> --addon-zip <verified-plugin.zip> --out <new-private-bundle.zip>
```

多个 profile 重复传入 `--profile`。输出必须是新文件，放在公共仓库之外。构建器按公开发布清单逐字节校验运行文件；私有合同中的原模板来源字段仅在包内相对化，原文件不改。随包保留两份模板、规范、候选 profile、插件 ZIP、安装器和逐文件 SHA。包内的原始 `.blend` 仍须禁用自动执行；本工具不打开它。

不复制旧 venv、GPU 权重、缓存、任务、原片、试验成片或 `config.local.json`。这些内容要么由目标机器重建，要么通过另行核对的项目迁移交接，不能混入工具包。客户资产只给获授权的电脑，不上传公共发布源。

## 在目标 Windows＋NVIDIA 电脑安装

完整解压后先运行：

```text
python install.py --plan-only --target <new-workspace> --skills-root <local-codex-skills>
python install.py --target <new-workspace> --skills-root <local-codex-skills>
```

也可通过随包 ASCII `install.bat` 传入相同参数。默认工作区在当前用户目录，技能目录读取本机 `CODEX_HOME`，否则使用当前用户 `.codex/skills`。先完整校验包、所有目标和路径，再创建文件；已有不同内容时拒绝覆盖，同字节文件允许重用。`catalog.json`、`installation.json` 在本机重新生成，旧机器绝对路径不会作为新配置沿用。

安装后的主 Skill 仍须执行自己的正常在线更新检查。安装器只部署文件，插件 ZIP 尚需在 Blender 中按该插件说明安装并验证；不自动启用。算法环境用内置 `scripts/motion/setup_backend.py` 的固定 Windows CUDA 配方，在选定工作区重新部署，并重新指定本机 Blender/FFmpeg。该步骤需要 Git、uv、网络、磁盘和兼容 NVIDIA 驱动；先看 `--help`、用 `--plan-only` 检查，再执行部署。

文件搬迁模拟、插件注册、后端环境安装、实际视频推理、指定模型接入和最终影片验收是不同证据。只有文件复制成功时，保持 `ready_for_inference=false`，不宣称新机器已经具备完整推理能力。正式使用前用短视频验证目标身份、动作、模型、场景、相机、颜色和导出，再启动全片。

## 交稿与内部记录

影片交稿遵守[交付与音轨](delivery-and-audio.md)。内部工具包和制作方 QA 保留真实状态；客户 ZIP 只收约定成果与中性使用说明，不附内部排错、自评、开发脚本或试验版本。不得为了省略内部记录而把未完成项登记为已通过。
