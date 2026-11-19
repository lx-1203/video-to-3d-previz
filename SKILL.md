---
name: cinematic-previz
metadata:
  author: "星迹"
  version: "0.6.0"
  plugin_version: "0.6.0"
description: Use when creating or revising 漫剧、短剧、导演分镜、Excel分镜与补图、Blender 白模预演、首帧生图、AI 视频、镜头接力、漫剧项目目录整理与回收恢复，或在 Updream、即梦使用 MCP 浏览器插件操作节点、多聊天分工和初始化工作区，或通过 LibTV 官方 Remote MCP 复用素材与整理画布；不用于单独建模或普通视频转码。
---

# 星迹的AIGC

作者：星迹。调用名 `$cinematic-previz`；内容及配对声明0.6.0不等于完整插件已发布，见[维护指南](references/maintenance-release.md)。按本次任务读取下表，不默认启动整片制作。

## 每次调用前检查同步

运行 `python "<skill-dir>/scripts/sync_from_project.py"`。仅在current/updated后重新读返回的SKILL和本次参考；同步器自身更新则再运行一次。失败保留改动与活锁并报告，不能绕过。独立模式见[运行规则](references/workflow.md#每次调用前确认独立模式或项目同步模式)。

## 范围与关键边界

每次读[core](references/core.md)，沿用已有原稿、版本、审核和授权。新项目进入制作按stages：文档剧本通过后做Excel/资产/首帧，含图Excel通过后生成视频，OpenCut剪辑；只写词或查询时完成当前交付。

资产按本次镜头的实际用途选择。人物、场景、表情、物品是分类，不是每镜必须凑齐的四套图；不因露脸、物件入画、旧台账有记录或参考栏为空就补图。已有整图足以表达的局部不重复裁出、上传或连接；历史辅助裁片保留档案，仅在明确需要独立控制且已核对有效时采用。

默认先核算朗读，再排每15秒一个multi_shot包；Excel主表一包一行，六项指导仅在“提示词指导”分表，不增“来源位置”列。完整词保留[分层格式与校验](references/prompts.md#提示词正文交付检查)。实际生成逐字取主表已审原词；缺素材直接告知用户，缺已审词或平台完整读回不一致时暂停该任务；continue原样用实际出点尾帧；未要求不制规划尾帧。未执行、缺素材与待审核如实标记。

Excel分镜默认交付含图版。先复用并嵌入有效图片，再自动制作缺失且必要的首帧与参考图，导出后复查图文位置。按[Excel实图补齐与摆放](references/director.md#excel实图补齐与摆放)执行，明确仅交文字或限改局部时按用户范围。

## 按任务读取

用 `python "<skill-dir>/scripts/read_modules.py" read <模块ID>` 读取，同脚本tree命令查ID；[索引](module-index.json)自动带入core、父节点和必要依赖。仅追加当前任务触发的专项；完整分镜保留必需设计与交付。

| 触发条件 | 读取模块 |
| --- | --- |
| 写/改视频提示词 | prompts；按正文里的写词/执行条件使用 |
| 制作/修改/交付Excel分镜 | storyboard；按[实图补齐与摆放](references/director.md#excel实图补齐与摆放)完成复用、补图、按栏嵌入与导出复查 |
| 查或改风格/表演/打光/声音 | 对应style / performance / lighting / sound |
| 确定使用某种运镜/转场 | 对应camera.<方法> / transitions.<方法> |
| 制作人物/表情/物品资产 | 对应character-assets / expression-assets / prop-assets |
| 制作首帧/三维预演 | previs.start / previs.3d |
| 排时长或核算朗读 | timing |
| 实际生成视频 | generation；任务间续接再加handoff |
| 实际操作某平台 | 只读相应platform模块；新增／整理Updream节点还读platform.layout，按实际尺寸分区排版并分别完成结构与视觉验收 |
| 整理记录/项目/交付 | 对应records / organization / delivery；完整制作读workflow |
| 采用课堂方法或查60项镜头 | camera.classroom；[课件适用条件](references/classroom-storyboard.md) |
| 修改skill或排查工具 | runtime及[维护指南](references/maintenance-release.md)；反馈按platform.feedback |

## 维护收尾

修改注册来源，验证后同步安装并读回。已有GitHub备份授权时，精确提交本轮范围、正常推送并验远端文件；失败记backup_pending。未授权不自行公开，不把skill_only说成完整Bridge插件。

## 可视化查看

需要查看状态或读取链时用[本机查看器](references/dashboard.md)。页面选择不代替正文条件、项目审核或执行授权。
