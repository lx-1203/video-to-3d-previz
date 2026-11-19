# 星迹的AIGC

作者：**星迹**。技术调用名：**cinematic-previz**。

## 推荐：Updream for Codex 自动安装

优先使用 Codex 时，下载 [Updream Codex 0.3.2 Windows 包](https://github.com/lx-1203/cinematic-previz/releases/tag/updream-codex-v0.3.2)，解压后双击 `install.bat`。安装器通过官方 Codex CLI 自动注册插件目录、安装并核对启用状态，无需 Edge 扩展文件夹选择或 JSON 配对。它复用 Codex 内置浏览器；正常登录、宿主权限及页面可用性仍需满足，安装成功不等于实站连接成功。详见 [Codex 安装说明](Codex-Updream/README.md)。

已有 CLI 的 GitHub 快装命令：

```text
codex plugin marketplace add lx-1203/video-to-3d-previz
codex plugin add updream-codex@xingji-updream
```

Windows ZIP 也支持[无人值守安装 Codex 应用插件](Codex-Updream/README.md#windows-无人值守安装)。

## Updream 浏览器插件：Windows 双击安装包

独立 Bridge **0.3.1** 已恢复完整 0.3.0 源码线并新增离线安装体验。前往 [Windows 安装包发布页](https://github.com/lx-1203/cinematic-previz/releases/tag/updream-bridge-v0.3.1)，下载 Windows x64 ZIP，完整解压后双击 `install.bat`。包内自带 Node 与生产依赖；本机服务/Codex 注册自动完成，浏览器按中文引导确认加载一次，扩展随后自动配对，不必导入 JSON 文件。详见 [安装说明](Updream-Node-Helper/INSTALL.md) 和 [来源范围](Updream-Node-Helper/PROVENANCE.md)。

此独立插件发布不等于历史 Bridge 0.4/0.6 源码恢复，也不改变下方技能 0.6.0 的内容版本。浏览器实站安装仍由使用者确认，安装器不会遥控桌面。

按[十步制作流程](references/workflow.md#制作阶段与审核顺序)先交传统文档剧本供用户审核，通过后制作Excel多列分镜、人物/场景/表情/物品资产和逐镜首帧；含图Excel获审核后再上传、连接节点，视频提示词逐字取自表内，不私自现写。尾帧未要求不自主生成；最后用OpenCut剪辑、配置字幕并导出，字幕默认宋体、白字黑边。已有通过版本沿用，不重复重做；当前“仅草稿/不提交”等限制继续有效。

## 版本与更新记录

当前本机技能内容版本：**0.6.0**；[release.json](release.json) 保留技能与 Updream MCP Bridge **0.6.0** 的历史配对声明，不能据此证明 Bridge 源码完整、Git 标签、远端提交或安装包已经发布。功能变化见 [更新记录](CHANGELOG.md)，旧日期编号保留。同步脚本的 SHA256 revision 核对本机具体内容；GitHub 备份另须取得远端 commit 与文件核验回执，见 [维护更新收尾](references/maintenance-release.md)。

## 普通用户：下载即可安装，不需要开发者项目

如果使用附带浏览器插件的技能整合包（旧版文件名 `AI漫剧-skill.zip`），解压后先读顶层 `安装说明.md`：顶层 `cinematic-previz` 是技能，`Updream-Node-Helper/extension` 是 Chrome / Edge 扩展，配套 MCP 服务也在该插件目录内。两部分分别安装；浏览器登录和本机配对由使用者完成。

1. 下载并解压技能包，找到直接包含 `SKILL.md` 的文件夹。
2. 将这个文件夹命名为 `cinematic-previz`，放进当前宿主的技能目录。Codex 使用配置的 `CODEX_HOME/skills`，未设置时使用用户主目录下的 `.codex/skills`；其他宿主按其自身的技能安装方式使用。
3. 确认结构是 `skills/cinematic-previz/SKILL.md`，而不是多嵌一层下载目录。更新已有技能前先备份并处理自己的改动，不覆盖其他技能。
4. 让宿主重新发现技能，或在新聊天调用：

```text
使用 $cinematic-previz（星迹的AIGC），根据这个剧本设计镜头和制作方案。
```

“星迹的AIGC”是界面显示名称；`$cinematic-previz` 是稳定调用标识。也可用自然语言说“使用星迹的AIGC”。只下载 ZIP、尚未解压安装时，宿主无法发现里面的技能。支持 Agent Skills 或可读取 Markdown 的宿主可以使用核心流程；具体调用界面由宿主提供。

普通用户**不需要** `.skill-source.json`、作者电脑目录、Git 仓库、Updream 账号或 API key 才能做导演分镜与提示词。基础流程不依赖某个视频平台；安装 skill 不会自动安装 Blender、浏览器扩展或生成模型。

## 运行能力按功能准备

| 使用内容 | 需要什么 |
| --- | --- |
| 文字分镜、镜头设计、提示词与接力规划 | 能加载技能或读取Markdown的AI宿主 |
| 逐镜首帧白模预演 | 宿主内置生图能力，Codex优先使用image_gen |
| 本地模式检查、可选项目同步 | Python 3.11+，只用标准库 |
| 精确尾帧提取 | Python 和 FFmpeg，可显式指定现有 FFmpeg 路径 |
| 白模实际渲染 | 本机 Blender 或可用桥接 |
| 实际生图/视频 | 当前宿主已提供的相应能力；平台和账号由使用者选择 |
| Updream 节点操作 | 单独安装并连接 Updream MCP Bridge，仅选择此平台时需要 |
| 即梦页面、画布草稿与协作 | 同一插件的即梦适配及逐站授权；先读 [即梦指南](references/jimeng-mcp.md)，按实际支持范围使用 |
| LibTV 素材复用与画布节点 | 复用宿主已授权的 LibTV 官方 Remote MCP；先读 [LibTV 指南](references/libtv-mcp.md)，按当前能力上传、整理与读回，不自动生成 |

缺少某项能力时明确标记该阶段未执行，继续能独立完成的部分；不要把规划文件称为已生成影片。若用户的 AGENTS 明确要求每次先执行同步检查，优先遵循该要求，失败不能跳过。

LibTV 是本技能新增的平台路由，账号与 OAuth 由宿主管理，未连接时按指南使用官方插件安装路线。该路由不表示 Updream 浏览器 Bridge 源码、配套安装包或实站验收已完成 LibTV 集成；发布号与配套版本保持 0.6.0。

人物资产新建或更新使用 [双参考五视图规范](references/character-assets.md)：图1负责面部，图2负责整套穿搭，缺少时先向用户索要。沿用已明确指定的参考无需重复上传；批量更新保留旧图并逐项记录生成与审图状态。

## 独立模式与可选项目同步

有命令能力时，可在任何工作目录运行实际安装位置的脚本：

```text
python "<实际技能目录>/scripts/sync_from_project.py"
```

| 条件/返回值 | 意义 |
| --- | --- |
| 没有注册文件，也未显式指定来源；current + standalone | 当前本地包可以独立使用，不写文件、不联网；revision 只是此包内容指纹，不表示远端最新 |
| 已注册来源；current + project | 安装内容与本次成功检查的本机来源项目一致，不表示 GitHub 已更新 |
| updated + project | 已同步；重新读取返回的 skill_file；若脚本自身更新，再运行一次 |
| update_available | 只比较模式发现更新，尚未应用 |
| LOCAL_CONFLICT / BUSY / 来源丢失 / 配置损坏 | 报告具体错误，保留本地修改和活锁；已经注册的用户不会自动降级绕过 |

### 维护者可选：从保留的源码目录更新

需要保留来源目录；同步目标必须与来源分开。来源既可以是独立技能根，也可以是大项目根下的 `skills/cinematic-previz`。

```text
python "<来源技能目录>/scripts/sync_from_project.py" --project-root "<保留的来源项目目录>" --target "<技能安装目录>/cinematic-previz" --register
```

注册使用真实路径，私有 `.skill-source.json`、备份和锁只留在安装端，不进入下载包。`--project-root` 或 `CINEMATIC_PREVIZ_PROJECT` 是显式来源选择；不传则读取已有注册。更换来源需要显式注册，已有冲突仍要先处理。不能自动把当前工作目录当作开发者项目。

同步使用 SHA256 内容指纹核对文件；本地独立修改触发冲突保护，更新先备份到 `.skill-backups/`。`--check` 只比较，不写文件。普通调用的脚本不会自动 git pull/push，`remote_checked=false` 只说明本机同步未检查远端；采用远端更新时先正常拉取合并，再同步。维护源码修改后的 GitHub 备份是独立的必做收尾：沿用既有发布授权完成精确范围提交、非强推上传和远端读回；阻塞时记录 `backup_pending`，不能把本机同步当成全部完成。普通下载用户不因此被要求向作者仓库推送，具体流程见 [维护更新收尾](references/maintenance-release.md)。确认没有活跃更新进程之前不能移除更新锁。

## 内容入口

| 入口 | 内容 |
| --- | --- |
| [SKILL.md](SKILL.md) | 核心流程、模式与能力选择 |
| [树状模块读取](references/module-loading.md) | 模块分类、横向依赖组合、预制Python读取器及完整交付要求 |
| [模块索引](module-index.json) | 分类树和章节/依赖的单一配置 |
| [完整工作流与检查](references/workflow.md) | 保留原有制作链、预算、真实尾帧与逐镜完整交付规则 |
| [维护更新收尾与 GitHub 备份](references/maintenance-release.md) | 普通调用与维护上传分工、沿用发布授权、远端核验回执及技能／完整插件范围 |
| [视觉风格](references/visual-style.md) | 从剧情推导风格与情绪；构图、色调、配色、材质及六项参数包 |
| [导演设计](references/director.md) | 专业分镜、逐镜完整提示词表、景别、机位、运镜和剪辑理由 |
| [高级镜头语言库](references/camera-language.md) | 16类机位、对焦与时间语言，完整提示词、复合镜头入表及质检/回退 |
| [六类电影感转场](references/transitions.md) | 动作/遮挡/形状/甩接/闪白/屏幕揭示，前后完整词、切点/合成与验收 |
| [表演提示词编译器](references/performance-compiler.md) | 逐拍表演、语音、正负向约束、结构化JSON与本地校验器 |
| [逐镜首帧白模](references/first-frame-previz.md) | Codex内置生图、角色身份色、起幅审图与镜号关联 |
| [完整分镜示例](references/storyboard-example.md) | 同镜号的叙事、视觉、预演与逐镜完整提示词 |
| [Blender 双预演](references/blender.md) | 导演预演与模型运动引导片 |
| [尾帧接力](references/frame-handoff.md) | 换机位、长镜头续接、实际剪辑出点 |
| [生成适配](references/generation.md) | 图片职责与目标工具能力 |
| [Updream 插件](references/updream-mcp.md) | 选用该平台时再读，接口以实际连接为准 |
| [即梦插件](references/jimeng-mcp.md) | 页面识别、画布批量操作、协作和已实现能力边界；节点ID不沿用Updream数值语义 |
| [LibTV 官方 MCP](references/libtv-mcp.md) | 官方连接、精确项目与右栏画布交接、既有素材复用、节点整理及仅草稿边界 |
| [初始提示词](references/updream-prompts.md) | 节点任务和跨聊天协作 |
| [来源和许可](references/sources.md) | 上游来源与保留的许可 |

有付费生成需求时遵循用户实际授权范围。源代码与流程公开不表示底层生成服务免费，也不包含任何人的登录态、素材或凭证。


## 规则归属与整理后的入口

SKILL.md仅保留同步、范围、路由和维护入口；分类及依赖仍由module-index.json维护。公共规则正式来源见 [规则归属](references/module-loading.md#公共规则的正式来源)。提示词正文已独立到 [prompts](references/prompts.md)，时间与完整句见 [timing](references/timing.md)，声音见 [sound](references/sound.md)。旧模块ID和迁移标题保留。完整分镜与实际制作依赖不删减，单项查询不自动读取整套制作流程。
