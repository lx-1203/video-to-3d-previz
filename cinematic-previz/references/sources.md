# 整合来源与适用边界

核对日期：2026-10-19。该 skill 是为当前用户整合编写的独立流程；不打包上游服务、原脚本或完整 skill，不要求安装整个上游仓库。

## DirectorSKILL

- 仓库：[wuwangzhang1216/DirectorSKILL](https://github.com/wuwangzhang1216/DirectorSKILL)
- 核对提交：`c65ae0d14457053efb1e354c7e7f7e120d97fad1`。
- [主 skill](https://github.com/wuwangzhang1216/DirectorSKILL/blob/c65ae0d14457053efb1e354c7e7f7e120d97fad1/SKILL.md)、[生产工作流](https://github.com/wuwangzhang1216/DirectorSKILL/blob/c65ae0d14457053efb1e354c7e7f7e120d97fad1/references/production-workflow.md)、[QC](https://github.com/wuwangzhang1216/DirectorSKILL/blob/c65ae0d14457053efb1e354c7e7f7e120d97fad1/assets/qc-checklist.md)。
- 借鉴：先确定镜头目的与调度，再准备关键帧和运动提示；生成前、单片段后、全片剪辑后分别检查。
- 调整：不采用其固定时长建议、评分阈值或导演风格目录作为普遍标准；按用户题材、实际模型和可用工具选择。

## Scenario 两种预演

- 仓库：[scenario-labs/skills](https://github.com/scenario-labs/skills)
- 核对提交：`ccc6c5c623a4f96423df0d157b8ef174f14edfd8`。
- [导演预演](https://github.com/scenario-labs/skills/blob/ccc6c5c623a4f96423df0d157b8ef174f14edfd8/skills/dcc/blender/scenario-blender-previs-storyboard/SKILL.md)：用粗场景、机位图和定时分镜判断信息与节奏。
- [生成引导白模](https://github.com/scenario-labs/skills/blob/ccc6c5c623a4f96423df0d157b8ef174f14edfd8/skills/dcc/blender/scenario-blender-blockout/SKILL.md)、[交接规则](https://github.com/scenario-labs/skills/blob/ccc6c5c623a4f96423df0d157b8ef174f14edfd8/skills/dcc/blender/scenario-blender-blockout/references/handoff.md)：把运动、位置、外观的参考职责分开，制作干净引导片并与结果并排核对。
- 调整：去除对 Scenario 插件、付费服务和固定 Blender 版本的强制依赖。多图风格板仅在合适的参考视频路线采用；不是所有首帧任务都必须做风格板。当前能力现场核实。

## 用户指定的衔接策略

需要换机位的剧情接点可从上一真实尾状态生成新角度首帧；超出单次生成时长的连续长镜头直接以真实尾帧续接。不是每节都换角度。该策略与版本指纹、动作状态和片段时间轴结合，是本 skill 的核心个性化部分，不宣称任何一种方式在所有镜头都最好。

上游 MIT 版权和许可全文保存在 [upstream-licenses.txt](upstream-licenses.txt)。本包的原创工具不含上游 Python 源码。

## 运镜与打光一手资料（2026-10-21）

- [M. David Mullen, ASC：Notes on Camera Movement](https://studentfilmmakers.com/notes-on-camera-movement-when-to-move-the-camera-and-why-by-m-david-mullen-asc/)：参考其从戏剧动机与剪辑需要判断何时运动的思考方式，不把个人焦距选择或具体影片镜头当成统一模板。
- [ARRI Lighting Handbook](https://www.arri.com/resource/blob/83996/409091c612f371b0c68b41d9dcb636db/arri-lighting-handbook-english-data.pdf)：用于核对软硬光及主光、补光、分离与背景光的区分。没有复制手册图片、完整章节或特定灯具配方。
- 场次视觉递进、逐镜运动/光线交付字段、AI能力适配与验收规则为本技能原创设计；不承诺仅凭术语即可得到某种电影效果。

## 视觉风格路由的依据与边界（2026-10-21）

- 十类检索入口由用户提出的全风格工作流改写，按其随后明确要求去掉所有姓名透镜，以剧情和情绪直接推导参数。不沿用目录中的固定焦距、帧率、颜色含义或媒介布光禁令，不声称目录穷尽所有风格。
- [RenderMan：Incredible Cinematography](https://prod.renderman.pixar.com/stories/incredible-cinematography)：制作团队讲述实拍参考、造型光与不同段落的色彩选择，支持将摄影思想转译为动画画面的做法，不将灯具类型限定为单一媒介配方。
- 风格参数包、构图与色调/配色分栏、剧情自动判断和逐镜色彩递进属于本技能的执行设计；示例中的焦距、颜色、光源与时间点是创作规划，不是某位创作者的固定公式，也不是生成结果保证。

## 表演编译模块（2026-10-21）

用户提供“变量、规则、模板、校验、回退”的模块设计与病房握手意图，本技能据此编写可执行结构与本地校验器。修正示例中的禁用词与多主动作自检矛盾，保留原句，按景别与当前模型能力适配。候选动作映射是创作策略，不是心理诊断或任一模型的效果保证；时间规划、声明计数和文字审读不能代替实际图像、声音与视频验收。

## 逐镜首帧白模预演（2026-10-21）

用户明确要求专业分镜同时生成对应首帧，以Codex内置生图制作白模、主角可上身份色。默认交付、起幅检查、颜色职责和版本映射据此编写。多视角生图可能改变结构，需实际目视核对；图像不能替代三维、动态、光照或真实尾帧证据。

## 逐镜提示词入表（2026-10-21）

用户指出分镜表未展示每镜对应提示词，现明确逐镜完整正文须在脚本表格中可见；模型请求、费用与历史日志可另存，但不能代替用户阅读的提示词表。实际调用原词与新规划文本、编辑目标和执行状态分别记录，不把重写后的文本冒充原图来源。

## 高级镜头语言库（2026-10-21）

用户要求补齐甩镜、主观移动、360°环绕、焦点转移、变速推进、细节到全景、上升环绕、前景擦过、慢镜头等执行规范。camera-language.md的类型路由、逐阶段字段、模型无关规划词、复合镜头入表、预演与回退为本技能原创工作流；示范不来自现有成片，未执行生成，不宣称已验证任何模型能力。机位移动、变焦、移焦与时间映射分别维护，具体接口/采样能力以实际制作时核实结果为准。

## 六类电影感转场（2026-10-21）

用户提供相似动作、前景遮挡、形状匹配、运动模糊/甩接、闪白与画中画/后拉揭示六类文字模板。本技能据此扩写transitions.md的双侧编译、切点、输入职责与质检回退；未据文字声称查看或核验其所述B站截图/原视频，也未确认任何品牌的通用转场参数。屏幕拉远与常规画中画、图形切与连续形变分别处理；示范仅为未执行规划，原片保真需要真实媒体与实际支持的合成路线。

## 树状模块架构与预置读取器（2026-10-21）

按用户要求将模块分类组织为树，跨专业分支以显式依赖组合，完整脚本的表格与每镜提示词保持不减。module-index.json和read_modules.py为本技能原创，只做本地目录/依赖/章节读取与来源记录；叙事选择仍由模型完成，不宣称改变宿主上下文机制或自动证明专业效果。旧入口详细规则迁移到workflow.md，原有专业参考和示例保持。
