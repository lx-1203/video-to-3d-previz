# 即梦 MCP 页面、画布与协作指南

适用：技能与 Updream MCP Bridge **0.6.0**。当前源码静态目录共71工具：33个Updream工具、12个多平台工具、9个即梦DOM画布工具、5个协作工具及12个官方CLI工具；网站原生目录与当前连接状态另行核对。工具名可能带宿主命名空间前缀，以实际tools/list参数为准。

## 任务边界与平台路由

仅插件开发、技能维护或版本核对，不调用或安排制作，不初始化工作区、不认领、不选择或移动用户节点、不生成。只有用户要求实际制作时，才在其明确项目和范围内操作；安装、更新、只读检查不构成制作或付费授权。

即梦走本模块的`jimeng_cli_*`官方通道或`media_*`DOM通道，节点ID是**字符串**；Updream数值nodeId及`updream_connect_nodes`等原生包装器不能替代即梦接口。通用的`updream_open_workspace`只用于建立独立工作区，不表示画布接口可以混用。保留其他导演、完整提示词、预算和逐镜交付规则。

## 先识别，再读取；制作才认领

1. `media_list_tabs`取得真实tabId，`media_detect_page`只读区分首页、首页画布入口、生成表单、已保存画布与资产页。网址推断、桥接ready不证明DOM已验证或已登录。
2. 生成界面用`media_get_page`读当前完整提示词与参数；已保存画布用`media_canvas_get_snapshot`读pageKey、revision、真实节点/连线ID。画布UUID来自实际URL；只读快照只覆盖已渲染DOM，不是完整后台图或历史。
3. 用户明确要求实际修改后，创建自己的`updream_open_workspace`并`media_claim_page`；画布写操作带当前expectedPageKey和最新expectedRevision。页面或快照变化先重读；`media_canvas_inspect_nodes`会选择节点，属于需认领的操作，不能冒充只读检查。
4. 生成表单的`media_set_prompt`使用实际expectedPrompt和完整新词；参数先用`media_open_options`读取实站菜单，再`media_select_option`选择唯一实际项并检查草稿保留。含富文本引用的提示词拒绝覆盖；有草稿/引用时不切换会重建表单的智能多帧。

同一已保存项目跨标签共享认领，只允许一个写执行者；不同项目可并行。不得复制别人的workspaceId或通过新标签绕开认领。服务重启后内存认领消失不代表旧操作已核对。

## 资产导入与批量整理

角色、场景、道具等资产级图片默认`media_canvas_import_images`的`mode:"nodes"`，显示为画布节点，不默认塞入视频节点。PNG/JPEG/WebP使用明确本地绝对路径；最多20张、每张8MiB、总计32MiB，全部预检后传送。接收成功不表示处理已完成。

节点内上传功能保留，但必须显式`mode:"references"`并提供nodeId；省略mode不会根据nodeId推断。首帧可以使用视频专用槽，但普通references上传不证明专用首帧ready或已建立真实连线。

`media_canvas_create_nodes`及nodes导入多个新节点默认`layout:"grid"`，也可显式`smart`或`none`。自动整理只针对本批实际返回的新ID，不移动用户既有节点；单节点不调用整理菜单。处理中图片可整理，但不能据此宣称图片已就绪。单独`media_canvas_layout`仅接受明确指定的2至100个唯一节点ID及当前revision。

`mode:"assets"`仅指即梦**原生资产库**，当前返回`NATIVE_ASSET_UPLOAD_UNAVAILABLE`。没有验证的上传入口就报告未实现，不以本地库或画布节点冒充原生资产入库。

## 新建相连视频、首帧与完整参数

`media_canvas_create_connected_video`从真实已就绪图片原生创建**相连的新视频草稿**；提供sourceNodeId、实际expectedSourceUrl，可附完整prompt及referenceMode（默认首尾帧，也可全能参考）。成功须核对新nodeId、edgeId、verified；传prompt时还须promptSaved，使用专用首帧时还须同源firstFrame.ready。不提交生成。

源图已有视频出边时返回`SOURCE_ALREADY_CONNECTED`，先核对实际目标，不重复新建。该工具不是将图片连接到任意已有视频的通用连线工具。

严格核对过的**单图片相连视频**可用`media_canvas_update_nodes`将全能参考切为首尾帧；以实际expected比较，保留完整提示词、时长、模型等受保护参数，比例允许原站自动匹配。多引用或图片加音频不属于此路径，不能强行套用。

`media_canvas_inspect_nodes`读取完整当前草稿及实时可选参数；`media_canvas_update_nodes`每个changes字段均提供实际expected。提示词写完整正文，不用摘要或截断快照覆盖；保留引用富文本保护。模型、时长、比例、分辨率等按当前实站选项，不猜值。批量部分失败核对completed/stoppedAt，不回滚已完成操作。

## 多Agent协作与未知结果

导入、连接规划、提示词、排版四类角色可并行准备，通过`media_collab_*`任务板与依赖交接，由一个执行者按项目顺序写入。插件不自行创建LLM Agent；协作状态仅存服务内存，重启不恢复或自动重放执行步骤。任意角色都不能因为分工而绕过认领、实时参数与实际能力限制。

`UNKNOWN_OUTCOME`等结果未知时，保留已知节点ID、回执和认领，只读核对已生效步骤；不盲目重放整个批次或再次创建/上传。pendingRequests=0不证明页面空闲或原操作未执行，注册表重启后为空也不是解决证据。存在进行中、认领或未知结果时，不为加载新版升级/重启共享服务；确认具体结果后再按现有释放规则交接。

## 只读诊断与尚未覆盖能力

`media_canvas_read_nodes`默认只读公开资源、状态及已绑定编辑器草稿；未绑定草稿明确unavailable。显式`includeDiagnostics:true`附公开DOM端口和矩形、包含隐藏项的DOM边、页面可见性/焦点及本适配器busy，不改变选择或焦点。最多100节点、每节点16端口、500边，超限声明截断；它不执行连接，也不证明完整后台连线或其他适配器空闲。

任意已有目标之间的原生连线、共享音频节点连接到指定已有视频尚未实现；公开端口/边诊断不等于写入能力，Updream数值连线接口不能代替。`media_canvas_get_results`和`media_get_results`仅返回当前可见媒体线索，不证明完整历史、原文件、下载完成或生成验收。

可灵、海螺、Vidu、Runway、Pika、Luma当前为发现级，不能宣称已完成表单、画布或生成接入。即梦本模块工具编辑草稿和参数，不提交生成；后续实际付费生成须另核实支持接口、用户授权与预算。本地开发检查通过不能写成实际媒体生成成功。

## 即梦官方CLI通道（0.6.0）

官方dreamina-canvas是外部程序，当前契约基于public/cn 1.1.1。先用jimeng_cli_status读取版本和Schema；未安装时按即梦官方安装页 https://jimeng.jianying.com/ai-tool/install?from_page=banner 获取，不用第三方Cookie反代代替官方授权。MCP工具名和参数以实时目录为准。离线可运行不能证明账号/会员/项目可用。

jimeng_cli_auth的status/account核对profile；需要登录时login返回官方授权URL、用户码和插件challengeId，wait只消费本会话挑战。不要复制设备码、Cookie、积分token或凭据。团队空间需通过官方CLI明确选择，插件只核验并固定既有空间。

用catalog读取规范模型/音色，用canvases按分页找明确UUID。实际制作才open_workspace并jimeng_cli_claim_canvas；CLI与DOM共享项目归属。所有业务工具显式profile/region/projectId，CLI不需要tabId或DOM revision。只读工具不建立工作区。

import_media上传后创建可见媒体节点，保存resourceId/nodeId/updateId；不是原生资产库入库。save_node创建/编辑图片、视频、音频、文本或主体草稿而不运行。编辑先nodes/show取得fingerprint。人物/场景/首帧通过references传真实有序node ID，回读完整prompt和upstreamNodeIds；首尾帧模式与m2v按实时模型能力分工，不把m2v说成硬锁首帧。

有正式Excel时，仍从所审版本完整单元格读取并逐字保存，references不要求私自追加或重写正文。保存后比较所有填写字段和引用；上游资源是否就绪另核，不能仅凭连线或导入回执宣称可生成。官方video flags未暴露独立音频开关，未知能力如实记录。

quote只读报价及draftVersion。用户当前授权覆盖后，run每次一个节点，显式给expectedDraftVersion、积分上限及唯一submitId。批次可以顺序调用，不静默超额。服务在重新核价和持久预留身份后只运行一次；返回受理不等于生成完成。使用operation查询原submitId、resource查询资源；积分快照不代表最终净扣费。当前CLI积分人民币换算须另外核实，不能套用Updream兑换率。

超时、部分失败或未知结果保留原身份和工作区保护，不自动重放，也不自动转DOM写第二次。运行恢复记录在插件.local/jimeng-submissions；不要删除记录绕过重复保护。结束仍使用已有release_canvas/close_workspace。当前协作任务计划执行仍走DOM，不虚构CLI队列功能。

本次未覆盖独立原生资产库、视频音频开关、时间轴写入和下载；真实登录、节点/连线持久化及付费生成需分别验收。仅更新插件/Skill不启动项目制作。
