# Updream MCP 接口列表 · 0.6.0

由 server/tools.cjs 自动生成。Codex 通过 MCP tools/list 获得同一套接口。

共 71 个接口：20 个直接接口、5 个效率接口、5 个多工作区协作接口、3 个素材接口、12 个多平台接口、9 个即梦画布接口、5 个即梦协作任务接口和12个即梦官方CLI接口。先用只读 media_detect_page 确认页面；省略 tabId 时只检测当前窗口唯一活动标签，多标签工作建议指定。需写入的工作先 open_workspace → claim_canvas；多平台页面使用 media_claim_page。所有写入携带 workspaceId。不同项目并行，同项目跨标签互斥；结束后 release_canvas 或 close_workspace。只读检查无需新建工作区。Updream和DOM节点接口必须指定真实 tabId；官方CLI按profile/region/projectId定位。即梦画布写入还需最新 pageKey/revision；批量部分失败不回滚，结果未知不重试。

以下 name/arguments 是 MCP 调用示例，不是可直接执行的生产计划。示例 workspaceId、tabId、projectId、nodeId、connectionId 和提示词均为虚构，实际操作必须替换为当前接口返回的真实值及用户授权内容；生成示例不会被验证器执行。原生名称与能力查当前 list_tools，业务约束由网站继续校验。

| 接口 | 只读 | 用途 |
| --- | --- | --- |
| updream_open_workspace | 否 | 为当前聊天或独立工作新建工作区。即使共享 MCP 进程，每项工作也应各自创建 workspaceId，随后认领画布。 |
| updream_list_workspaces | 是 | 查看各工作区、任务名称、会话在线状态和画布归属；同项目跨标签页共享认领。用于分工与交接。 |
| updream_claim_canvas | 否 | 把实际项目画布认领给本工作区，跨多次调用保留。其他工作区不能同时写同项目，即使使用不同标签页。不同项目可并行。 |
| updream_release_canvas | 否 | 结束本项目操作后释放认领交接。进行中的写入不能释放；结果未知时先读回核对，再显式 acknowledgeUnknownOutcome。 |
| updream_close_workspace | 否 | 工作结束后关闭并释放所有认领。recoverAbandoned 仅能清理已断开或 90 秒无心跳的其他会话；仍在写入时拒绝清理。结果未知需先核对再确认。 |
| updream_connection_status | 是 | 检查本机 MCP 与浏览器插件的连接，不返回配对凭证。 |
| updream_list_tabs | 是 | 列出已打开的 updream 标签页及其接口状态。先取真实 tabId，再读取快照和操作节点。 |
| updream_list_tools | 是 | 查询标签页实时原生工具。detail=names只读名称，summary省略Schema；toolNames按点/下划线名称筛选。默认返回完整目录。 |
| updream_get_snapshot | 是 | 读取画布节点、连线与选中项。提示词为摘要；truncated 时用 nextOffset 继续分页。 |
| updream_get_node | 是 | 读取单节点详情、完整编辑草稿与生成历史。保存提示词前优先读 editDraft.prompt，摘要可能截断。 |
| updream_create_node | 否 | 新建 image/video/text 节点，可保存提示词草稿，不触发生成。返回 nodeId；核对 promptSaved。 |
| updream_update_node | 否 | 修改节点名称、提示词、模型、比例或参考图，至少提供一项。不会触发生成；修改后读回核对。 |
| updream_connect_nodes | 否 | 从上游连接到下游节点，默认作为参考素材；先读取快照取得真实 ID。 |
| updream_disconnect | 否 | 删除一条画布连线，可通过 undo 撤销；connectionId 来自快照。 |
| updream_group_nodes | 否 | 将至少两个节点分组，最多 50 个；返回组合节点 ID。 |
| updream_move_node | 否 | 把节点移到画布坐标 x/y，非屏幕像素坐标。 |
| updream_select_nodes | 否 | 选择指定节点；focus=true 将视口聚焦到第一个节点。只改界面选中态。 |
| updream_undo | 否 | 撤销上一步画布操作；无可撤销操作时网站报错。 |
| updream_redo | 否 | 重做上一次撤销的画布操作。 |
| updream_list_models | 是 | 查询账号实际可用的图片/视频/文本模型和参数能力，不要猜模型名。 |
| updream_create_image_task | 否 | 向图片节点提交生成，可能消耗积分，保留网站确认。返回任务号不是生成完成；禁止失败或超时后盲目重试。 |
| updream_create_video_task | 否 | 向视频节点提交生成，可能消耗积分，保留网站确认。先查询模型能力，提交后用 get_task_status 查询。 |
| updream_create_text_task | 否 | 向文本节点提交生成，可能消耗积分，保留网站确认。完成后 get_node 读取 textContent。 |
| updream_get_task_status | 是 | 读取画布生成任务状态、进度与结果。nodeId/taskId 至少一项；建议查询间隔不少于 5 秒，不重复创建任务。 |
| updream_cancel_task | 否 | 取消指定节点正在进行的生成任务；不自动重新生成。 |
| updream_upload_files | 否 | 上传明确指定的本地 PNG/JPEG/WebP 图片到当前项目素材库，按内容去重并读回核对；单张8MiB、总计32MiB、最多20张。可选nodeId创建图片素材节点并作为普通参考连入，保留已有参考，不配置专用首帧、不入虚拟角色库、不生成。部分失败查看completed，结果未知不重试。 |
| updream_list_assets | 是 | 列出本插件在当前项目已核对的上传缓存：SHA256、assetId、URL和名称。不读取本地文件、不返回路径，当前远端存活性由复用时再次核对。 |
| updream_reuse_assets | 否 | 按当前项目缓存SHA256复用图片到指定节点：先远端核对，再复用或创建图片节点及参考连线，并读回验证。不重新上传、不覆盖已有参考、不配置专用首帧、不生成。 |
| updream_find_nodes | 是 | 跨页搜索节点名称、ID、提示词摘要，支持类型和状态筛选。返回 nextOffset 继续扫描；达到 maxScan 时明确标记。不是全文搜索或原子快照。 |
| updream_call_native_tool | 否 | 按实时目录调用已注册的 canvas/gen/materials 工具，先验证网页 JSON Schema，再保留网站业务校验与确认。写入仍要求工作区认领。 |
| updream_batch | 否 | 最多 20 步本工作区画布顺序调用。先验证全部实时 JSON Schema，业务校验仍在执行时进行。遇错、拒绝或取消停止并返回 completed/stoppedAt。不是事务，不回滚或自动重试。 |
| updream_update_node_checked | 否 | 在已认领画布修改名称/完整提示词前比较 expected，写后读回核对。每个 changes 字段都需对应 expected；提示词必须来自完整 editDraft.prompt。仍非网站原子条件更新。 |
| updream_diagnose | 是 | 诊断本机、浏览器和可选标签页的版本、启用状态及原生工具注册情况，不返回令牌。 |
| media_open_project | 否 | 打开明确UUID对应的已保存即梦画布；复用同项目标签或新开固定官方地址，不导航其他项目、不新建画布。opening需随后只读核对，发生开页后不得盲目重试。 |
| media_detect_page | 是 | 只读检测页面类型：即梦首页、首页画布入口、生成表单、画布编辑器或资产页，返回DOM确认状态、弹窗及适用工具提示。省略tabId检测已连接浏览器当前窗口唯一活动标签；多标签工作建议显式指定。缺权限或旧脚本只按网址推断，不读取草稿、不认领、不刷新、不生成。其他平台具体页面未验证时返回unknown。 |
| media_platform_status | 是 | 只读查询指定平台的站点授权、动态脚本和可见标签状态。缺权限时 visibleTabCount=null，不能据此断言未开页面。ready 仅表示桥接就绪，不代表已登录。 |
| media_open_platform | 否 | 打开目录内平台的固定入口；不接受任意网址。openAuthorizationPage=true 时，缺权限或脚本会打开本插件预选授权页供用户点击；不静默授权、不登录、不生成。不得盲目重试开页。 |
| media_list_platforms | 是 | 列出统一平台目录和验证级别。discovery 仅能识别页面；不代表已接通表单或生成。 |
| media_list_tabs | 是 | 列出已授权且已打开的视频平台标签页及适配状态；旧 updream_list_tabs 仍只返回 Updream。 |
| media_get_page | 是 | 读取页面平台、pageKey、完整当前提示词和可见参数。discovery 平台仅返回发现信息。DOM 草稿不代表服务器已保存。 |
| media_claim_page | 否 | 使用现有工作区认领当前平台页面，防止多个聊天覆盖同一页。页面切换后先释放旧认领。 |
| media_set_prompt | 否 | 在已认领即梦页面比较原提示词后编辑草稿并读回。含参考标签的富文本拒绝覆盖；不提交生成。 |
| media_open_options | 否 | 打开当前参数的实时菜单，返回 menuId 和完整选项文字。时长只提供面板中有效的刻度按钮；format 分别列出实际比例与分辨率单选项。不明确的菜单请手动选择。 |
| media_select_option | 否 | 选择刚打开且唯一匹配的实际选项。返回选中验证、draftPreserved、changedControls 和当前参数；草稿未保留时停止并核对。智能多帧在有草稿/参考时拒绝切换。submitted=false；创作类型切换后重新读取与认领。 |
| media_get_results | 是 | 读取即梦当前可见页面中的视频链接。仅当前 DOM，不是完整历史、后台任务查询或下载完成证明。 |
| media_canvas_create_connected_video | 否 | 从已就绪的真实图片节点原生创建一个相连新视频草稿，核对新nodeId/edgeId和同源参考，可配置首尾帧并核对专用首帧ready。不是连接任意已有视频。源已连视频时拒绝重复创建；创建后失败保留回执，结果未知不重放，不生成。 |
| media_canvas_read_nodes | 是 | 只读已渲染节点的公开资源、状态和当前绑定编辑器草稿；未绑定草稿明确 unavailable。显式includeDiagnostics可读取公开连接端口、包含隐藏项的DOM边、页面可见性和本适配器busy，仅为诊断，不代表后台完整图。不改变选择或焦点。 |
| media_canvas_get_results | 是 | 只读画布可见资源链接，区分缩略图、blob与观察到的媒体源；不代表原文件、完整历史或下载完成。 |
| media_canvas_get_snapshot | 是 | 只读当前已保存的即梦画布中已渲染的节点、连线、选中项和 revision。不是完整后台项目；写入前先认领并携带当前 pageKey/revision。 |
| media_canvas_inspect_nodes | 否 | 在已认领画布依次选择并读取最多20个节点的完整草稿、引用状态和参数，可选读取实时参数选项。选择会改变界面，需当前 pageKey/revision；不生成。 |
| media_canvas_create_nodes | 否 | 批量创建1至20个文本/图片/视频节点并保存正文或草稿；多个新节点默认原生grid整理，仅整理本批新ID。可显式选smart或none关闭整理；单节点不整理。不提交生成；部分失败核对completed/stoppedAt，未知不重试。 |
| media_canvas_layout | 否 | 对明确指定的2至100个唯一节点使用原生宫格 grid 或智能 smart 整理。先取真实节点 ID 与最新 revision；不生成。 |
| media_canvas_update_nodes | 否 | 逐节点比较 expected 后更新完整提示词或实际可用参数，最多20个；每个修改字段都需对应 expected。保留已有引用，含引用提示词拒绝覆盖；部分失败停止，不回滚、不生成。 |
| media_canvas_import_images | 否 | 资产级PNG/JPEG/WebP默认导入为可见画布nodes，多个新节点默认grid整理，可显式smart或none。节点内上传能力保留：需明确选择references并提供nodeId，不根据nodeId推断；references不布局，首帧槽需另行核对。assets仅指原生资产库，尚无验证入口，返回NATIVE_ASSET_UPLOAD_UNAVAILABLE，不用本地库替代。最多20张、单张8MiB、总32MiB，全部文件预检后传送；接受不等于处理完成，不生成。 |
| media_collab_team | 否 | 创建即梦画布协作团队、用邀请加入或由原执行者close释放。关闭删除共享草稿和邀请；running/unknown/waiting/未核对failed时拒绝关闭。各会话用自己的workspace，创建者需已认领画布。服务不启动LLM。 |
| media_collab_board | 是 | 读取本团队角色、依赖、版本和回执，默认省略草稿、路径及首帧源URL；includePayload仅显式读取共享计划。connect只支持从已有图片创建原生相连新视频。 |
| media_collab_task | 否 | 创建、领取、提交、取消任务。recoverAbandoned仅接管离线超过90秒且未执行的claimed准备。原执行者可acknowledgeStopped确认failed停止，不重试、不解锁依赖、不清unknown。需最新expectedVersion；动作create/import/update/layout/connect，connect从已有图片创建原生相连新视频，不能连任意已有目标。 |
| media_collab_execute_next | 否 | 仅团队创建者且原画布认领者顺序执行下个就绪任务，须唯一requestId及明确核对当前画布的expectedRevision。原样校验版本，不自动接受用户后续改动。重复请求返回原收据；部分失败/未知冻结，不自动重试，不是事务。 |
| media_collab_reconcile | 否 | 对waiting导入的已知节点重新只读核对ready状态。证据不足保持阻塞；unknown不自动恢复或重放，不能清除原workspace未知保护。 |
| jimeng_cli_status | 是 | 只读检查官方CLI版本和命令目录；不登录、不访问画布。CLI需独立安装，不能把离线可运行当实站通过。 |
| jimeng_cli_auth | 否 | 官方CLI认证：status/account只读，login取得官方授权链接，wait使用本会话challengeId等待。设备码仅在内存，不导出Cookie或凭据；不强制重登录。 |
| jimeng_cli_catalog | 是 | 读取当前模型/音色目录；model list必须选type，query按官方find解析规范模型名，不猜参数或音色。 |
| jimeng_cli_canvases | 是 | 只读列出当前CLI profile可访问画布，不选择或创建画布；分页保留nextCursor。 |
| jimeng_cli_claim_canvas | 否 | 核验官方账号/空间及指定项目访问后认领；与网页DOM通道共享同项目互斥，不虚构tabId。不创建画布。 |
| jimeng_cli_nodes | 是 | 只读查找或读取官方节点。show返回fingerprint用于编辑前比较；不改变选择，不生成。 |
| jimeng_cli_save_node | 否 | 创建/编辑官方typed节点草稿并读回校验字段和有序节点引用，不带run。需稳定nodeId/updateId；编辑要求最新fingerprint。失败/未知保留原ID，不重放。 |
| jimeng_cli_import_media | 否 | 上传单个本地图片/视频/音频为项目资源并创建独立可见节点，不是原生资产库入库。需稳定resourceId/nodeId/updateId；回读不代表媒体处理已完成。 |
| jimeng_cli_quote | 是 | 只读取得已保存节点的完整积分报价及draftVersion，不批准、不生成；积分与人民币换算由实际账户证据另核。 |
| jimeng_cli_run | 否 | 按既有用户授权运行一个已保存节点，可能消耗积分。要求期望draftVersion、明确积分上限和唯一submitId；先重新报价，内部使用批准token，持久预留身份防重复。返回受理不等于完成。 |
| jimeng_cli_operation | 是 | 只读查询原submitId对应的真实操作与资源，不重新提交；absent/unknown均不能作为安全重提证明。 |
| jimeng_cli_resource | 是 | 只读查询指定资源状态和元信息，不下载、不运行。积分快照不代表最终净扣费。 |

## updream_open_workspace

为当前聊天或独立工作新建工作区。即使共享 MCP 进程，每项工作也应各自创建 workspaceId，随后认领画布。

```json
{
  "type": "object",
  "properties": {
    "label": {
      "type": "string",
      "minLength": 1,
      "maxLength": 120
    }
  },
  "required": [
    "label"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_open_workspace",
  "arguments": {
    "label": "示例：角色节点整理"
  }
}
```

## updream_list_workspaces

查看各工作区、任务名称、会话在线状态和画布归属；同项目跨标签页共享认领。用于分工与交接。

```json
{
  "type": "object",
  "properties": {},
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_list_workspaces",
  "arguments": {}
}
```

## updream_claim_canvas

把实际项目画布认领给本工作区，跨多次调用保留。其他工作区不能同时写同项目，即使使用不同标签页。不同项目可并行。

```json
{
  "type": "object",
  "properties": {
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "每个聊天/工作单独 open_workspace 获得，写入前先认领画布"
    },
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    }
  },
  "required": [
    "workspaceId",
    "tabId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_claim_canvas",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7
  }
}
```

## updream_release_canvas

结束本项目操作后释放认领交接。进行中的写入不能释放；结果未知时先读回核对，再显式 acknowledgeUnknownOutcome。

```json
{
  "type": "object",
  "properties": {
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "每个聊天/工作单独 open_workspace 获得，写入前先认领画布"
    },
    "projectId": {
      "type": "string",
      "minLength": 1
    },
    "acknowledgeUnknownOutcome": {
      "default": false,
      "type": "boolean"
    }
  },
  "required": [
    "workspaceId",
    "projectId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_release_canvas",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "projectId": "123",
    "acknowledgeUnknownOutcome": false
  }
}
```

## updream_close_workspace

工作结束后关闭并释放所有认领。recoverAbandoned 仅能清理已断开或 90 秒无心跳的其他会话；仍在写入时拒绝清理。结果未知需先核对再确认。

```json
{
  "type": "object",
  "properties": {
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "每个聊天/工作单独 open_workspace 获得，写入前先认领画布"
    },
    "recoverAbandoned": {
      "default": false,
      "type": "boolean"
    },
    "acknowledgeUnknownOutcome": {
      "default": false,
      "type": "boolean"
    }
  },
  "required": [
    "workspaceId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_close_workspace",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "recoverAbandoned": false,
    "acknowledgeUnknownOutcome": false
  }
}
```

## updream_connection_status

检查本机 MCP 与浏览器插件的连接，不返回配对凭证。

```json
{
  "type": "object",
  "properties": {},
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_connection_status",
  "arguments": {}
}
```

## updream_list_tabs

列出已打开的 updream 标签页及其接口状态。先取真实 tabId，再读取快照和操作节点。

```json
{
  "type": "object",
  "properties": {},
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_list_tabs",
  "arguments": {}
}
```

## updream_list_tools

查询标签页实时原生工具。detail=names只读名称，summary省略Schema；toolNames按点/下划线名称筛选。默认返回完整目录。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "toolNames": {
      "minItems": 1,
      "maxItems": 30,
      "type": "array",
      "items": {
        "type": "string",
        "pattern": "^(canvas|gen|materials)[._][A-Za-z][A-Za-z0-9]*$"
      }
    },
    "detail": {
      "type": "string",
      "enum": [
        "names",
        "summary",
        "full"
      ]
    }
  },
  "required": [
    "tabId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_list_tools",
  "arguments": {
    "tabId": 7
  }
}
```

## updream_get_snapshot

读取画布节点、连线与选中项。提示词为摘要；truncated 时用 nextOffset 继续分页。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "nodeIds": {
      "maxItems": 200,
      "type": "array",
      "items": {
        "type": "integer",
        "exclusiveMinimum": 0,
        "maximum": 9007199254740991,
        "description": "从当前画布快照取得的真实节点 ID"
      }
    },
    "includeEdits": {
      "type": "boolean"
    },
    "limit": {
      "type": "integer",
      "minimum": 1,
      "maximum": 200
    },
    "offset": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991
    }
  },
  "required": [
    "tabId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_get_snapshot",
  "arguments": {
    "tabId": 7,
    "limit": 100,
    "offset": 0
  }
}
```

## updream_get_node

读取单节点详情、完整编辑草稿与生成历史。保存提示词前优先读 editDraft.prompt，摘要可能截断。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "nodeId": {
      "type": "integer",
      "exclusiveMinimum": 0,
      "maximum": 9007199254740991,
      "description": "从当前画布快照取得的真实节点 ID"
    }
  },
  "required": [
    "tabId",
    "nodeId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_get_node",
  "arguments": {
    "tabId": 7,
    "nodeId": 101
  }
}
```

## updream_create_node

新建 image/video/text 节点，可保存提示词草稿，不触发生成。返回 nodeId；核对 promptSaved。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "每个聊天/工作单独 open_workspace 获得，写入前先认领画布"
    },
    "nodeType": {
      "type": "string",
      "enum": [
        "image",
        "video",
        "text"
      ]
    },
    "name": {
      "type": "string",
      "minLength": 1,
      "maxLength": 100
    },
    "prompt": {
      "type": "string",
      "maxLength": 20000
    },
    "position": {
      "type": "object",
      "properties": {
        "x": {
          "type": "number"
        },
        "y": {
          "type": "number"
        }
      },
      "required": [
        "x",
        "y"
      ],
      "additionalProperties": false
    }
  },
  "required": [
    "tabId",
    "workspaceId",
    "nodeType"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_create_node",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "nodeType": "image",
    "name": "角色草稿",
    "prompt": "完整新提示词",
    "position": {
      "x": 100,
      "y": 200
    }
  }
}
```

## updream_update_node

修改节点名称、提示词、模型、比例或参考图，至少提供一项。不会触发生成；修改后读回核对。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "每个聊天/工作单独 open_workspace 获得，写入前先认领画布"
    },
    "nodeId": {
      "type": "integer",
      "exclusiveMinimum": 0,
      "maximum": 9007199254740991,
      "description": "从当前画布快照取得的真实节点 ID"
    },
    "name": {
      "type": "string",
      "minLength": 1,
      "maxLength": 100
    },
    "prompt": {
      "type": "string",
      "maxLength": 20000
    },
    "model": {
      "type": "string",
      "minLength": 1
    },
    "ratio": {
      "type": "string",
      "minLength": 1
    },
    "referenceImages": {
      "maxItems": 20,
      "type": "array",
      "items": {
        "type": "string",
        "format": "uri"
      }
    }
  },
  "required": [
    "tabId",
    "workspaceId",
    "nodeId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_update_node",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "nodeId": 101,
    "name": "角色草稿 v2"
  }
}
```

## updream_connect_nodes

从上游连接到下游节点，默认作为参考素材；先读取快照取得真实 ID。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "每个聊天/工作单独 open_workspace 获得，写入前先认领画布"
    },
    "fromNodeId": {
      "type": "integer",
      "exclusiveMinimum": 0,
      "maximum": 9007199254740991,
      "description": "从当前画布快照取得的真实节点 ID"
    },
    "toNodeId": {
      "type": "integer",
      "exclusiveMinimum": 0,
      "maximum": 9007199254740991,
      "description": "从当前画布快照取得的真实节点 ID"
    },
    "isReference": {
      "type": "boolean"
    }
  },
  "required": [
    "tabId",
    "workspaceId",
    "fromNodeId",
    "toNodeId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_connect_nodes",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "fromNodeId": 101,
    "toNodeId": 102,
    "isReference": true
  }
}
```

## updream_disconnect

删除一条画布连线，可通过 undo 撤销；connectionId 来自快照。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "每个聊天/工作单独 open_workspace 获得，写入前先认领画布"
    },
    "connectionId": {
      "type": "integer",
      "exclusiveMinimum": 0,
      "maximum": 9007199254740991,
      "description": "从当前画布快照取得的真实节点 ID"
    }
  },
  "required": [
    "tabId",
    "workspaceId",
    "connectionId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_disconnect",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "connectionId": 301
  }
}
```

## updream_group_nodes

将至少两个节点分组，最多 50 个；返回组合节点 ID。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "每个聊天/工作单独 open_workspace 获得，写入前先认领画布"
    },
    "nodeIds": {
      "minItems": 2,
      "maxItems": 50,
      "type": "array",
      "items": {
        "type": "integer",
        "exclusiveMinimum": 0,
        "maximum": 9007199254740991,
        "description": "从当前画布快照取得的真实节点 ID"
      }
    }
  },
  "required": [
    "tabId",
    "workspaceId",
    "nodeIds"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_group_nodes",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "nodeIds": [
      101,
      102
    ]
  }
}
```

## updream_move_node

把节点移到画布坐标 x/y，非屏幕像素坐标。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "每个聊天/工作单独 open_workspace 获得，写入前先认领画布"
    },
    "nodeId": {
      "type": "integer",
      "exclusiveMinimum": 0,
      "maximum": 9007199254740991,
      "description": "从当前画布快照取得的真实节点 ID"
    },
    "x": {
      "type": "number"
    },
    "y": {
      "type": "number"
    }
  },
  "required": [
    "tabId",
    "workspaceId",
    "nodeId",
    "x",
    "y"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_move_node",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "nodeId": 101,
    "x": 300,
    "y": 200
  }
}
```

## updream_select_nodes

选择指定节点；focus=true 将视口聚焦到第一个节点。只改界面选中态。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "每个聊天/工作单独 open_workspace 获得，写入前先认领画布"
    },
    "nodeIds": {
      "maxItems": 200,
      "type": "array",
      "items": {
        "type": "integer",
        "exclusiveMinimum": 0,
        "maximum": 9007199254740991,
        "description": "从当前画布快照取得的真实节点 ID"
      }
    },
    "focus": {
      "type": "boolean"
    }
  },
  "required": [
    "tabId",
    "workspaceId",
    "nodeIds"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_select_nodes",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "nodeIds": [
      101
    ],
    "focus": true
  }
}
```

## updream_undo

撤销上一步画布操作；无可撤销操作时网站报错。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "每个聊天/工作单独 open_workspace 获得，写入前先认领画布"
    }
  },
  "required": [
    "tabId",
    "workspaceId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_undo",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7
  }
}
```

## updream_redo

重做上一次撤销的画布操作。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "每个聊天/工作单独 open_workspace 获得，写入前先认领画布"
    }
  },
  "required": [
    "tabId",
    "workspaceId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_redo",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7
  }
}
```

## updream_list_models

查询账号实际可用的图片/视频/文本模型和参数能力，不要猜模型名。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "kind": {
      "type": "string",
      "enum": [
        "image",
        "video",
        "text"
      ]
    }
  },
  "required": [
    "tabId",
    "kind"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_list_models",
  "arguments": {
    "tabId": 7,
    "kind": "video"
  }
}
```

## updream_create_image_task

向图片节点提交生成，可能消耗积分，保留网站确认。返回任务号不是生成完成；禁止失败或超时后盲目重试。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "每个聊天/工作单独 open_workspace 获得，写入前先认领画布"
    },
    "nodeId": {
      "type": "integer",
      "exclusiveMinimum": 0,
      "maximum": 9007199254740991,
      "description": "从当前画布快照取得的真实节点 ID"
    },
    "prompt": {
      "type": "string",
      "minLength": 1,
      "maxLength": 20000
    },
    "model": {
      "type": "string",
      "minLength": 1
    },
    "ratio": {
      "type": "string",
      "minLength": 1
    },
    "referenceImages": {
      "maxItems": 20,
      "type": "array",
      "items": {
        "type": "string",
        "format": "uri"
      }
    },
    "count": {
      "type": "integer",
      "minimum": 1,
      "maximum": 4
    }
  },
  "required": [
    "tabId",
    "workspaceId",
    "nodeId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_create_image_task",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "nodeId": 101,
    "prompt": "已核对的图片提示词",
    "count": 1
  }
}
```

## updream_create_video_task

向视频节点提交生成，可能消耗积分，保留网站确认。先查询模型能力，提交后用 get_task_status 查询。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "每个聊天/工作单独 open_workspace 获得，写入前先认领画布"
    },
    "nodeId": {
      "type": "integer",
      "exclusiveMinimum": 0,
      "maximum": 9007199254740991,
      "description": "从当前画布快照取得的真实节点 ID"
    },
    "prompt": {
      "type": "string",
      "minLength": 1,
      "maxLength": 20000
    },
    "model": {
      "type": "string",
      "minLength": 1
    },
    "duration": {
      "type": "number",
      "exclusiveMinimum": 0
    },
    "resolution": {
      "type": "string",
      "minLength": 1
    },
    "aspectRatio": {
      "type": "string",
      "minLength": 1
    },
    "image": {
      "type": "string",
      "format": "uri"
    }
  },
  "required": [
    "tabId",
    "workspaceId",
    "nodeId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_create_video_task",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "nodeId": 102,
    "prompt": "已核对的动作与摄影机提示词"
  }
}
```

## updream_create_text_task

向文本节点提交生成，可能消耗积分，保留网站确认。完成后 get_node 读取 textContent。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "每个聊天/工作单独 open_workspace 获得，写入前先认领画布"
    },
    "nodeId": {
      "type": "integer",
      "exclusiveMinimum": 0,
      "maximum": 9007199254740991,
      "description": "从当前画布快照取得的真实节点 ID"
    },
    "prompt": {
      "type": "string",
      "minLength": 1,
      "maxLength": 20000
    },
    "model": {
      "type": "string",
      "minLength": 1
    }
  },
  "required": [
    "tabId",
    "workspaceId",
    "nodeId",
    "prompt"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_create_text_task",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "nodeId": 103,
    "prompt": "已授权的文本生成任务"
  }
}
```

## updream_get_task_status

读取画布生成任务状态、进度与结果。nodeId/taskId 至少一项；建议查询间隔不少于 5 秒，不重复创建任务。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "nodeId": {
      "type": "integer",
      "exclusiveMinimum": 0,
      "maximum": 9007199254740991,
      "description": "从当前画布快照取得的真实节点 ID"
    },
    "taskId": {
      "type": "string",
      "minLength": 1
    }
  },
  "required": [
    "tabId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_get_task_status",
  "arguments": {
    "tabId": 7,
    "nodeId": 102
  }
}
```

## updream_cancel_task

取消指定节点正在进行的生成任务；不自动重新生成。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "每个聊天/工作单独 open_workspace 获得，写入前先认领画布"
    },
    "nodeId": {
      "type": "integer",
      "exclusiveMinimum": 0,
      "maximum": 9007199254740991,
      "description": "从当前画布快照取得的真实节点 ID"
    }
  },
  "required": [
    "tabId",
    "workspaceId",
    "nodeId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_cancel_task",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "nodeId": 102
  }
}
```

## updream_upload_files

上传明确指定的本地 PNG/JPEG/WebP 图片到当前项目素材库，按内容去重并读回核对；单张8MiB、总计32MiB、最多20张。可选nodeId创建图片素材节点并作为普通参考连入，保留已有参考，不配置专用首帧、不入虚拟角色库、不生成。部分失败查看completed，结果未知不重试。

```json
{
  "type": "object",
  "properties": {
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "每个聊天/工作单独 open_workspace 获得，写入前先认领画布"
    },
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "files": {
      "minItems": 1,
      "maxItems": 20,
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1
      }
    },
    "nodeId": {
      "type": "integer",
      "exclusiveMinimum": 0,
      "maximum": 9007199254740991,
      "description": "从当前画布快照取得的真实节点 ID"
    }
  },
  "required": [
    "workspaceId",
    "tabId",
    "files"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_upload_files",
  "arguments": {
    "workspaceId": "12345678-1234-4234-8234-123456789abc",
    "tabId": 123,
    "files": [
      "D:/Film/SH07/first.png"
    ],
    "nodeId": 456
  }
}
```

## updream_list_assets

列出本插件在当前项目已核对的上传缓存：SHA256、assetId、URL和名称。不读取本地文件、不返回路径，当前远端存活性由复用时再次核对。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    }
  },
  "required": [
    "tabId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_list_assets",
  "arguments": {
    "tabId": 123
  }
}
```

## updream_reuse_assets

按当前项目缓存SHA256复用图片到指定节点：先远端核对，再复用或创建图片节点及参考连线，并读回验证。不重新上传、不覆盖已有参考、不配置专用首帧、不生成。

```json
{
  "type": "object",
  "properties": {
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "每个聊天/工作单独 open_workspace 获得，写入前先认领画布"
    },
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "nodeId": {
      "type": "integer",
      "exclusiveMinimum": 0,
      "maximum": 9007199254740991,
      "description": "从当前画布快照取得的真实节点 ID"
    },
    "hashes": {
      "minItems": 1,
      "maxItems": 20,
      "type": "array",
      "items": {
        "type": "string",
        "pattern": "^[a-f0-9]{64}$"
      }
    }
  },
  "required": [
    "workspaceId",
    "tabId",
    "nodeId",
    "hashes"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_reuse_assets",
  "arguments": {
    "workspaceId": "12345678-1234-4234-8234-123456789abc",
    "tabId": 123,
    "nodeId": 457,
    "hashes": [
      "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    ]
  }
}
```

## updream_find_nodes

跨页搜索节点名称、ID、提示词摘要，支持类型和状态筛选。返回 nextOffset 继续扫描；达到 maxScan 时明确标记。不是全文搜索或原子快照。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "query": {
      "default": "",
      "type": "string",
      "maxLength": 500
    },
    "type": {
      "type": "string",
      "minLength": 1
    },
    "status": {
      "type": "string",
      "minLength": 1
    },
    "offset": {
      "default": 0,
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991
    },
    "limit": {
      "default": 50,
      "type": "integer",
      "minimum": 1,
      "maximum": 200
    },
    "maxScan": {
      "default": 2000,
      "type": "integer",
      "minimum": 1,
      "maximum": 10000
    }
  },
  "required": [
    "tabId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_find_nodes",
  "arguments": {
    "tabId": 7,
    "query": "角色",
    "type": "image",
    "limit": 20,
    "maxScan": 2000,
    "offset": 0
  }
}
```

## updream_call_native_tool

按实时目录调用已注册的 canvas/gen/materials 工具，先验证网页 JSON Schema，再保留网站业务校验与确认。写入仍要求工作区认领。

```json
{
  "type": "object",
  "properties": {
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "每个聊天/工作单独 open_workspace 获得，写入前先认领画布"
    },
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "tool": {
      "type": "string",
      "pattern": "^(canvas|gen|materials)[._][A-Za-z][A-Za-z0-9]*$",
      "description": "来自实时目录的 canvas/gen/materials 名称"
    },
    "arguments": {
      "default": {},
      "type": "object",
      "propertyNames": {
        "type": "string"
      },
      "additionalProperties": {
        "$ref": "#/$defs/__schema0"
      }
    }
  },
  "required": [
    "workspaceId",
    "tabId",
    "tool"
  ],
  "additionalProperties": false,
  "$defs": {
    "__schema0": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "number"
        },
        {
          "type": "boolean"
        },
        {
          "type": "null"
        },
        {
          "type": "array",
          "items": {
            "$ref": "#/$defs/__schema0"
          }
        },
        {
          "type": "object",
          "propertyNames": {
            "type": "string"
          },
          "additionalProperties": {
            "$ref": "#/$defs/__schema0"
          }
        }
      ]
    }
  }
}
```

调用示例：

```json
{
  "name": "updream_call_native_tool",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "tool": "canvas.getNode",
    "arguments": {
      "nodeId": 101
    }
  }
}
```

## updream_batch

最多 20 步本工作区画布顺序调用。先验证全部实时 JSON Schema，业务校验仍在执行时进行。遇错、拒绝或取消停止并返回 completed/stoppedAt。不是事务，不回滚或自动重试。

```json
{
  "type": "object",
  "properties": {
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "每个聊天/工作单独 open_workspace 获得，写入前先认领画布"
    },
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "steps": {
      "minItems": 1,
      "maxItems": 20,
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "tool": {
            "type": "string",
            "pattern": "^(canvas|gen|materials)[._][A-Za-z][A-Za-z0-9]*$",
            "description": "来自实时目录的 canvas/gen/materials 名称"
          },
          "arguments": {
            "default": {},
            "type": "object",
            "propertyNames": {
              "type": "string"
            },
            "additionalProperties": {
              "$ref": "#/$defs/__schema0"
            }
          }
        },
        "required": [
          "tool"
        ],
        "additionalProperties": false
      }
    }
  },
  "required": [
    "workspaceId",
    "tabId",
    "steps"
  ],
  "additionalProperties": false,
  "$defs": {
    "__schema0": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "number"
        },
        {
          "type": "boolean"
        },
        {
          "type": "null"
        },
        {
          "type": "array",
          "items": {
            "$ref": "#/$defs/__schema0"
          }
        },
        {
          "type": "object",
          "propertyNames": {
            "type": "string"
          },
          "additionalProperties": {
            "$ref": "#/$defs/__schema0"
          }
        }
      ]
    }
  }
}
```

调用示例：

```json
{
  "name": "updream_batch",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "steps": [
      {
        "tool": "canvas.moveNode",
        "arguments": {
          "nodeId": 101,
          "x": 300,
          "y": 200
        }
      },
      {
        "tool": "canvas.moveNode",
        "arguments": {
          "nodeId": 102,
          "x": 700,
          "y": 200
        }
      }
    ]
  }
}
```

## updream_update_node_checked

在已认领画布修改名称/完整提示词前比较 expected，写后读回核对。每个 changes 字段都需对应 expected；提示词必须来自完整 editDraft.prompt。仍非网站原子条件更新。

```json
{
  "type": "object",
  "properties": {
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "每个聊天/工作单独 open_workspace 获得，写入前先认领画布"
    },
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    },
    "nodeId": {
      "type": "integer",
      "exclusiveMinimum": 0,
      "maximum": 9007199254740991,
      "description": "从当前画布快照取得的真实节点 ID"
    },
    "expected": {
      "type": "object",
      "properties": {
        "name": {
          "type": "string",
          "minLength": 1,
          "maxLength": 100
        },
        "prompt": {
          "type": "string",
          "maxLength": 20000
        }
      },
      "additionalProperties": false
    },
    "changes": {
      "type": "object",
      "properties": {
        "name": {
          "type": "string",
          "minLength": 1,
          "maxLength": 100
        },
        "prompt": {
          "type": "string",
          "maxLength": 20000
        }
      },
      "additionalProperties": false
    }
  },
  "required": [
    "workspaceId",
    "tabId",
    "nodeId",
    "expected",
    "changes"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_update_node_checked",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "nodeId": 101,
    "expected": {
      "prompt": "从 get_node 读取的完整原稿"
    },
    "changes": {
      "prompt": "完整的新稿"
    }
  }
}
```

## updream_diagnose

诊断本机、浏览器和可选标签页的版本、启用状态及原生工具注册情况，不返回令牌。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 updream_list_tabs 取得的浏览器标签页 ID，必须显式指定"
    }
  },
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "updream_diagnose",
  "arguments": {
    "tabId": 7
  }
}
```

## media_open_project

打开明确UUID对应的已保存即梦画布；复用同项目标签或新开固定官方地址，不导航其他项目、不新建画布。opening需随后只读核对，发生开页后不得盲目重试。

```json
{
  "type": "object",
  "properties": {
    "platformId": {
      "type": "string",
      "const": "jimeng"
    },
    "projectId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "openAuthorizationPage": {
      "default": false,
      "type": "boolean"
    }
  },
  "required": [
    "platformId",
    "projectId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_open_project",
  "arguments": {
    "platformId": "jimeng",
    "projectId": "11111111-1111-4111-8111-111111111111",
    "openAuthorizationPage": false
  }
}
```

## media_detect_page

只读检测页面类型：即梦首页、首页画布入口、生成表单、画布编辑器或资产页，返回DOM确认状态、弹窗及适用工具提示。省略tabId检测已连接浏览器当前窗口唯一活动标签；多标签工作建议显式指定。缺权限或旧脚本只按网址推断，不读取草稿、不认领、不刷新、不生成。其他平台具体页面未验证时返回unknown。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 media_list_tabs 取得的真实标签页 ID"
    }
  },
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_detect_page",
  "arguments": {
    "tabId": 7
  }
}
```

## media_platform_status

只读查询指定平台的站点授权、动态脚本和可见标签状态。缺权限时 visibleTabCount=null，不能据此断言未开页面。ready 仅表示桥接就绪，不代表已登录。

```json
{
  "type": "object",
  "properties": {
    "platformId": {
      "type": "string",
      "enum": [
        "jimeng",
        "kling",
        "hailuo",
        "vidu",
        "runway",
        "pika",
        "luma"
      ]
    }
  },
  "required": [
    "platformId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_platform_status",
  "arguments": {
    "platformId": "jimeng"
  }
}
```

## media_open_platform

打开目录内平台的固定入口；不接受任意网址。openAuthorizationPage=true 时，缺权限或脚本会打开本插件预选授权页供用户点击；不静默授权、不登录、不生成。不得盲目重试开页。

```json
{
  "type": "object",
  "properties": {
    "platformId": {
      "type": "string",
      "enum": [
        "jimeng",
        "kling",
        "hailuo",
        "vidu",
        "runway",
        "pika",
        "luma"
      ]
    },
    "openAuthorizationPage": {
      "default": false,
      "type": "boolean"
    }
  },
  "required": [
    "platformId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_open_platform",
  "arguments": {
    "platformId": "jimeng",
    "openAuthorizationPage": true
  }
}
```

## media_list_platforms

列出统一平台目录和验证级别。discovery 仅能识别页面；不代表已接通表单或生成。

```json
{
  "type": "object",
  "properties": {},
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_list_platforms",
  "arguments": {}
}
```

## media_list_tabs

列出已授权且已打开的视频平台标签页及适配状态；旧 updream_list_tabs 仍只返回 Updream。

```json
{
  "type": "object",
  "properties": {},
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_list_tabs",
  "arguments": {}
}
```

## media_get_page

读取页面平台、pageKey、完整当前提示词和可见参数。discovery 平台仅返回发现信息。DOM 草稿不代表服务器已保存。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 media_list_tabs 取得的真实标签页 ID"
    }
  },
  "required": [
    "tabId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_get_page",
  "arguments": {
    "tabId": 7
  }
}
```

## media_claim_page

使用现有工作区认领当前平台页面，防止多个聊天覆盖同一页。页面切换后先释放旧认领。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 media_list_tabs 取得的真实标签页 ID"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "使用现有 updream_open_workspace 新建，每项工作单独使用"
    }
  },
  "required": [
    "tabId",
    "workspaceId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_claim_page",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7
  }
}
```

## media_set_prompt

在已认领即梦页面比较原提示词后编辑草稿并读回。含参考标签的富文本拒绝覆盖；不提交生成。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 media_list_tabs 取得的真实标签页 ID"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "使用现有 updream_open_workspace 新建，每项工作单独使用"
    },
    "expectedPageKey": {
      "type": "string",
      "minLength": 1,
      "maxLength": 4096,
      "description": "来自 media_get_page 的完整 pageKey；页面改变后重新认领"
    },
    "expectedPrompt": {
      "type": "string",
      "maxLength": 20000
    },
    "prompt": {
      "type": "string",
      "maxLength": 20000
    }
  },
  "required": [
    "tabId",
    "workspaceId",
    "expectedPageKey",
    "expectedPrompt",
    "prompt"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_set_prompt",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "expectedPageKey": "https://jimeng.jianying.com/ai-tool/home?type=video",
    "expectedPrompt": "",
    "prompt": "镜头缓慢推近"
  }
}
```

## media_open_options

打开当前参数的实时菜单，返回 menuId 和完整选项文字。时长只提供面板中有效的刻度按钮；format 分别列出实际比例与分辨率单选项。不明确的菜单请手动选择。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 media_list_tabs 取得的真实标签页 ID"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "使用现有 updream_open_workspace 新建，每项工作单独使用"
    },
    "expectedPageKey": {
      "type": "string",
      "minLength": 1,
      "maxLength": 4096,
      "description": "来自 media_get_page 的完整 pageKey；页面改变后重新认领"
    },
    "control": {
      "type": "string",
      "enum": [
        "model",
        "creationType",
        "referenceMode",
        "duration",
        "format"
      ]
    },
    "expectedValue": {
      "type": "string",
      "maxLength": 2000
    }
  },
  "required": [
    "tabId",
    "workspaceId",
    "expectedPageKey",
    "control",
    "expectedValue"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_open_options",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "expectedPageKey": "https://jimeng.jianying.com/ai-tool/home?type=video",
    "control": "model",
    "expectedValue": "即梦 Seedance 2.0 VIP"
  }
}
```

## media_select_option

选择刚打开且唯一匹配的实际选项。返回选中验证、draftPreserved、changedControls 和当前参数；草稿未保留时停止并核对。智能多帧在有草稿/参考时拒绝切换。submitted=false；创作类型切换后重新读取与认领。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 media_list_tabs 取得的真实标签页 ID"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "使用现有 updream_open_workspace 新建，每项工作单独使用"
    },
    "expectedPageKey": {
      "type": "string",
      "minLength": 1,
      "maxLength": 4096,
      "description": "来自 media_get_page 的完整 pageKey；页面改变后重新认领"
    },
    "menuId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "value": {
      "type": "string",
      "minLength": 1,
      "maxLength": 4000
    }
  },
  "required": [
    "tabId",
    "workspaceId",
    "expectedPageKey",
    "menuId",
    "value"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_select_option",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "expectedPageKey": "https://jimeng.jianying.com/ai-tool/home?type=video",
    "menuId": "11111111-1111-4111-8111-111111111111",
    "value": "网页刚返回的完整模型选项"
  }
}
```

## media_get_results

读取即梦当前可见页面中的视频链接。仅当前 DOM，不是完整历史、后台任务查询或下载完成证明。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 media_list_tabs 取得的真实标签页 ID"
    }
  },
  "required": [
    "tabId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_get_results",
  "arguments": {
    "tabId": 7
  }
}
```

## media_canvas_create_connected_video

从已就绪的真实图片节点原生创建一个相连新视频草稿，核对新nodeId/edgeId和同源参考，可配置首尾帧并核对专用首帧ready。不是连接任意已有视频。源已连视频时拒绝重复创建；创建后失败保留回执，结果未知不重放，不生成。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 media_list_tabs 取得的真实标签页 ID"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "expectedPageKey": {
      "type": "string",
      "minLength": 1,
      "maxLength": 4096
    },
    "expectedRevision": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "来自最新画布快照的 revision，页面变化后重新读取"
    },
    "sourceNodeId": {
      "type": "string",
      "minLength": 1,
      "maxLength": 160
    },
    "expectedSourceUrl": {
      "type": "string",
      "maxLength": 8192,
      "format": "uri"
    },
    "prompt": {
      "type": "string",
      "maxLength": 20000
    },
    "referenceMode": {
      "default": "首尾帧",
      "type": "string",
      "enum": [
        "首尾帧",
        "全能参考"
      ]
    }
  },
  "required": [
    "tabId",
    "workspaceId",
    "expectedPageKey",
    "expectedRevision",
    "sourceNodeId",
    "expectedSourceUrl"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_canvas_create_connected_video",
  "arguments": {
    "tabId": 7,
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "expectedPageKey": "https://jimeng.jianying.com/ai-tool/ai-canvas/22222222-2222-4222-8222-222222222222",
    "expectedRevision": "33333333-3333-4333-8333-333333333333",
    "sourceNodeId": "node_source_example",
    "expectedSourceUrl": "https://example.com/frame.png",
    "prompt": "示例草稿，实际替换为授权的完整执行词",
    "referenceMode": "首尾帧"
  }
}
```

## media_canvas_read_nodes

只读已渲染节点的公开资源、状态和当前绑定编辑器草稿；未绑定草稿明确 unavailable。显式includeDiagnostics可读取公开连接端口、包含隐藏项的DOM边、页面可见性和本适配器busy，仅为诊断，不代表后台完整图。不改变选择或焦点。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 media_list_tabs 取得的真实标签页 ID"
    },
    "nodeIds": {
      "minItems": 1,
      "maxItems": 100,
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1,
        "maxLength": 160
      }
    },
    "includeDiagnostics": {
      "description": "仅显式true附加公共DOM端口/边与页面状态；不执行连接",
      "type": "boolean"
    }
  },
  "required": [
    "tabId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_canvas_read_nodes",
  "arguments": {
    "tabId": 7,
    "nodeIds": [
      "node_example"
    ]
  }
}
```

## media_canvas_get_results

只读画布可见资源链接，区分缩略图、blob与观察到的媒体源；不代表原文件、完整历史或下载完成。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 media_list_tabs 取得的真实标签页 ID"
    },
    "nodeIds": {
      "minItems": 1,
      "maxItems": 100,
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1,
        "maxLength": 160
      }
    }
  },
  "required": [
    "tabId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_canvas_get_results",
  "arguments": {
    "tabId": 7
  }
}
```

## media_canvas_get_snapshot

只读当前已保存的即梦画布中已渲染的节点、连线、选中项和 revision。不是完整后台项目；写入前先认领并携带当前 pageKey/revision。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 media_list_tabs 取得的真实标签页 ID"
    }
  },
  "required": [
    "tabId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_canvas_get_snapshot",
  "arguments": {
    "tabId": 7
  }
}
```

## media_canvas_inspect_nodes

在已认领画布依次选择并读取最多20个节点的完整草稿、引用状态和参数，可选读取实时参数选项。选择会改变界面，需当前 pageKey/revision；不生成。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 media_list_tabs 取得的真实标签页 ID"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "expectedPageKey": {
      "type": "string",
      "minLength": 1,
      "maxLength": 4096
    },
    "expectedRevision": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "来自最新画布快照的 revision，页面变化后重新读取"
    },
    "nodeIds": {
      "minItems": 1,
      "maxItems": 20,
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1,
        "maxLength": 160
      }
    },
    "includeOptions": {
      "default": [],
      "type": "array",
      "items": {
        "type": "string",
        "enum": [
          "model",
          "ratio",
          "resolution",
          "quantity",
          "referenceMode",
          "duration"
        ]
      }
    }
  },
  "required": [
    "tabId",
    "workspaceId",
    "expectedPageKey",
    "expectedRevision",
    "nodeIds"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_canvas_inspect_nodes",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "expectedPageKey": "https://jimeng.jianying.com/ai-tool/ai-canvas/22222222-2222-4222-8222-222222222222",
    "expectedRevision": "33333333-3333-4333-8333-333333333333",
    "nodeIds": [
      "example-video-node"
    ],
    "includeOptions": [
      "model",
      "ratio",
      "duration"
    ]
  }
}
```

## media_canvas_create_nodes

批量创建1至20个文本/图片/视频节点并保存正文或草稿；多个新节点默认原生grid整理，仅整理本批新ID。可显式选smart或none关闭整理；单节点不整理。不提交生成；部分失败核对completed/stoppedAt，未知不重试。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 media_list_tabs 取得的真实标签页 ID"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "expectedPageKey": {
      "type": "string",
      "minLength": 1,
      "maxLength": 4096
    },
    "expectedRevision": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "来自最新画布快照的 revision，页面变化后重新读取"
    },
    "nodes": {
      "minItems": 1,
      "maxItems": 20,
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "type": {
            "type": "string",
            "enum": [
              "text",
              "image",
              "video"
            ]
          },
          "text": {
            "type": "string",
            "maxLength": 20000
          },
          "prompt": {
            "type": "string",
            "maxLength": 20000
          }
        },
        "required": [
          "type"
        ],
        "additionalProperties": false
      }
    },
    "layout": {
      "default": "grid",
      "type": "string",
      "enum": [
        "grid",
        "smart",
        "none"
      ]
    }
  },
  "required": [
    "tabId",
    "workspaceId",
    "expectedPageKey",
    "expectedRevision",
    "nodes"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_canvas_create_nodes",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "expectedPageKey": "https://jimeng.jianying.com/ai-tool/ai-canvas/22222222-2222-4222-8222-222222222222",
    "expectedRevision": "33333333-3333-4333-8333-333333333333",
    "nodes": [
      {
        "type": "text",
        "text": "示例镜头说明"
      },
      {
        "type": "image",
        "prompt": "已授权的首帧草稿"
      },
      {
        "type": "video",
        "prompt": "已授权的动作草稿"
      }
    ],
    "layout": "grid"
  }
}
```

## media_canvas_layout

对明确指定的2至100个唯一节点使用原生宫格 grid 或智能 smart 整理。先取真实节点 ID 与最新 revision；不生成。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 media_list_tabs 取得的真实标签页 ID"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "expectedPageKey": {
      "type": "string",
      "minLength": 1,
      "maxLength": 4096
    },
    "expectedRevision": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "来自最新画布快照的 revision，页面变化后重新读取"
    },
    "nodeIds": {
      "minItems": 2,
      "maxItems": 100,
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1,
        "maxLength": 160
      }
    },
    "layout": {
      "type": "string",
      "enum": [
        "grid",
        "smart"
      ]
    }
  },
  "required": [
    "tabId",
    "workspaceId",
    "expectedPageKey",
    "expectedRevision",
    "nodeIds",
    "layout"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_canvas_layout",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "expectedPageKey": "https://jimeng.jianying.com/ai-tool/ai-canvas/22222222-2222-4222-8222-222222222222",
    "expectedRevision": "33333333-3333-4333-8333-333333333333",
    "nodeIds": [
      "example-image-node",
      "example-video-node"
    ],
    "layout": "smart"
  }
}
```

## media_canvas_update_nodes

逐节点比较 expected 后更新完整提示词或实际可用参数，最多20个；每个修改字段都需对应 expected。保留已有引用，含引用提示词拒绝覆盖；部分失败停止，不回滚、不生成。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 media_list_tabs 取得的真实标签页 ID"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "expectedPageKey": {
      "type": "string",
      "minLength": 1,
      "maxLength": 4096
    },
    "expectedRevision": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "来自最新画布快照的 revision，页面变化后重新读取"
    },
    "updates": {
      "minItems": 1,
      "maxItems": 20,
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "nodeId": {
            "type": "string",
            "minLength": 1,
            "maxLength": 160
          },
          "expected": {
            "type": "object",
            "properties": {
              "prompt": {
                "type": "string",
                "maxLength": 20000
              },
              "model": {
                "type": "string",
                "maxLength": 4000
              },
              "ratio": {
                "type": "string",
                "maxLength": 2000
              },
              "resolution": {
                "type": "string",
                "maxLength": 2000
              },
              "quantity": {
                "type": "string",
                "maxLength": 2000
              },
              "referenceMode": {
                "type": "string",
                "maxLength": 2000
              },
              "duration": {
                "type": "string",
                "maxLength": 2000
              }
            },
            "additionalProperties": false
          },
          "changes": {
            "type": "object",
            "properties": {
              "prompt": {
                "type": "string",
                "maxLength": 20000
              },
              "model": {
                "type": "string",
                "maxLength": 4000
              },
              "ratio": {
                "type": "string",
                "maxLength": 2000
              },
              "resolution": {
                "type": "string",
                "maxLength": 2000
              },
              "quantity": {
                "type": "string",
                "maxLength": 2000
              },
              "referenceMode": {
                "type": "string",
                "maxLength": 2000
              },
              "duration": {
                "type": "string",
                "maxLength": 2000
              }
            },
            "additionalProperties": false
          }
        },
        "required": [
          "nodeId",
          "expected",
          "changes"
        ],
        "additionalProperties": false
      }
    }
  },
  "required": [
    "tabId",
    "workspaceId",
    "expectedPageKey",
    "expectedRevision",
    "updates"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_canvas_update_nodes",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "expectedPageKey": "https://jimeng.jianying.com/ai-tool/ai-canvas/22222222-2222-4222-8222-222222222222",
    "expectedRevision": "33333333-3333-4333-8333-333333333333",
    "updates": [
      {
        "nodeId": "example-video-node",
        "expected": {
          "prompt": "刚读取的完整原稿",
          "duration": "5s"
        },
        "changes": {
          "prompt": "完整新稿",
          "duration": "10s"
        }
      }
    ]
  }
}
```

## media_canvas_import_images

资产级PNG/JPEG/WebP默认导入为可见画布nodes，多个新节点默认grid整理，可显式smart或none。节点内上传能力保留：需明确选择references并提供nodeId，不根据nodeId推断；references不布局，首帧槽需另行核对。assets仅指原生资产库，尚无验证入口，返回NATIVE_ASSET_UPLOAD_UNAVAILABLE，不用本地库替代。最多20张、单张8MiB、总32MiB，全部文件预检后传送；接受不等于处理完成，不生成。

```json
{
  "type": "object",
  "properties": {
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991,
      "description": "从 media_list_tabs 取得的真实标签页 ID"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "expectedPageKey": {
      "type": "string",
      "minLength": 1,
      "maxLength": 4096
    },
    "expectedRevision": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "来自最新画布快照的 revision，页面变化后重新读取"
    },
    "mode": {
      "default": "nodes",
      "type": "string",
      "enum": [
        "nodes",
        "assets",
        "references"
      ]
    },
    "files": {
      "minItems": 1,
      "maxItems": 20,
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1
      }
    },
    "nodeId": {
      "type": "string",
      "minLength": 1,
      "maxLength": 160
    },
    "layout": {
      "description": "仅nodes可提供；nodes省略时默认grid，none关闭本批整理",
      "type": "string",
      "enum": [
        "grid",
        "smart",
        "none"
      ]
    }
  },
  "required": [
    "tabId",
    "workspaceId",
    "expectedPageKey",
    "expectedRevision",
    "files"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_canvas_import_images",
  "arguments": {
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "expectedPageKey": "https://jimeng.jianying.com/ai-tool/ai-canvas/22222222-2222-4222-8222-222222222222",
    "expectedRevision": "33333333-3333-4333-8333-333333333333",
    "mode": "references",
    "nodeId": "example-video-node",
    "files": [
      "D:/Film/SH07/first.png",
      "D:/Film/SH07/second.webp"
    ]
  }
}
```

## media_collab_team

创建即梦画布协作团队、用邀请加入或由原执行者close释放。关闭删除共享草稿和邀请；running/unknown/waiting/未核对failed时拒绝关闭。各会话用自己的workspace，创建者需已认领画布。服务不启动LLM。

```json
{
  "oneOf": [
    {
      "type": "object",
      "properties": {
        "op": {
          "type": "string",
          "const": "create"
        },
        "workspaceId": {
          "type": "string",
          "format": "uuid",
          "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
        },
        "tabId": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "role": {
          "type": "string",
          "enum": [
            "import",
            "connect",
            "prompt",
            "layout"
          ]
        }
      },
      "required": [
        "op",
        "workspaceId",
        "tabId",
        "role"
      ],
      "additionalProperties": false
    },
    {
      "type": "object",
      "properties": {
        "op": {
          "type": "string",
          "const": "join"
        },
        "teamId": {
          "type": "string",
          "format": "uuid",
          "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
        },
        "workspaceId": {
          "type": "string",
          "format": "uuid",
          "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
        },
        "inviteToken": {
          "type": "string",
          "pattern": "^[a-f0-9]{64}$"
        },
        "role": {
          "type": "string",
          "enum": [
            "import",
            "connect",
            "prompt",
            "layout"
          ]
        }
      },
      "required": [
        "op",
        "teamId",
        "workspaceId",
        "inviteToken",
        "role"
      ],
      "additionalProperties": false
    },
    {
      "type": "object",
      "properties": {
        "op": {
          "type": "string",
          "const": "close"
        },
        "teamId": {
          "type": "string",
          "format": "uuid",
          "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
        },
        "workspaceId": {
          "type": "string",
          "format": "uuid",
          "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
        }
      },
      "required": [
        "op",
        "teamId",
        "workspaceId"
      ],
      "additionalProperties": false
    }
  ]
}
```

调用示例：

```json
{
  "name": "media_collab_team",
  "arguments": {
    "op": "create",
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "role": "import"
  }
}
```

## media_collab_board

读取本团队角色、依赖、版本和回执，默认省略草稿、路径及首帧源URL；includePayload仅显式读取共享计划。connect只支持从已有图片创建原生相连新视频。

```json
{
  "type": "object",
  "properties": {
    "teamId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "includePayload": {
      "default": false,
      "type": "boolean"
    },
    "offset": {
      "default": 0,
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991
    },
    "limit": {
      "default": 50,
      "type": "integer",
      "minimum": 1,
      "maximum": 100
    }
  },
  "required": [
    "teamId",
    "workspaceId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_collab_board",
  "arguments": {
    "teamId": "22222222-2222-4222-8222-222222222222",
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "includePayload": false,
    "offset": 0,
    "limit": 50
  }
}
```

## media_collab_task

创建、领取、提交、取消任务。recoverAbandoned仅接管离线超过90秒且未执行的claimed准备。原执行者可acknowledgeStopped确认failed停止，不重试、不解锁依赖、不清unknown。需最新expectedVersion；动作create/import/update/layout/connect，connect从已有图片创建原生相连新视频，不能连任意已有目标。

```json
{
  "oneOf": [
    {
      "type": "object",
      "properties": {
        "teamId": {
          "type": "string",
          "format": "uuid",
          "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
        },
        "workspaceId": {
          "type": "string",
          "format": "uuid",
          "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
        },
        "op": {
          "type": "string",
          "const": "create"
        },
        "expectedVersion": {
          "default": 0,
          "type": "number",
          "const": 0
        },
        "role": {
          "type": "string",
          "enum": [
            "import",
            "connect",
            "prompt",
            "layout"
          ]
        },
        "title": {
          "type": "string",
          "minLength": 1,
          "maxLength": 120
        },
        "dependsOn": {
          "default": [],
          "maxItems": 20,
          "type": "array",
          "items": {
            "type": "string",
            "format": "uuid",
            "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
          }
        }
      },
      "required": [
        "teamId",
        "workspaceId",
        "op",
        "role",
        "title"
      ],
      "additionalProperties": false
    },
    {
      "type": "object",
      "properties": {
        "teamId": {
          "type": "string",
          "format": "uuid",
          "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
        },
        "workspaceId": {
          "type": "string",
          "format": "uuid",
          "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
        },
        "taskId": {
          "type": "string",
          "format": "uuid",
          "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
        },
        "expectedVersion": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "op": {
          "type": "string",
          "const": "claim"
        },
        "recoverAbandoned": {
          "default": false,
          "type": "boolean"
        }
      },
      "required": [
        "teamId",
        "workspaceId",
        "taskId",
        "expectedVersion",
        "op"
      ],
      "additionalProperties": false
    },
    {
      "type": "object",
      "properties": {
        "teamId": {
          "type": "string",
          "format": "uuid",
          "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
        },
        "workspaceId": {
          "type": "string",
          "format": "uuid",
          "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
        },
        "taskId": {
          "type": "string",
          "format": "uuid",
          "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
        },
        "expectedVersion": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "op": {
          "type": "string",
          "const": "submit"
        },
        "plan": {
          "oneOf": [
            {
              "type": "object",
              "properties": {
                "action": {
                  "type": "string",
                  "const": "create"
                },
                "arguments": {
                  "type": "object",
                  "properties": {
                    "nodes": {
                      "minItems": 1,
                      "maxItems": 20,
                      "type": "array",
                      "items": {
                        "type": "object",
                        "properties": {
                          "type": {
                            "type": "string",
                            "enum": [
                              "text",
                              "image",
                              "video"
                            ]
                          },
                          "text": {
                            "type": "string",
                            "maxLength": 20000
                          },
                          "prompt": {
                            "type": "string",
                            "maxLength": 20000
                          }
                        },
                        "required": [
                          "type"
                        ],
                        "additionalProperties": false
                      }
                    },
                    "layout": {
                      "default": "grid",
                      "type": "string",
                      "enum": [
                        "grid",
                        "smart",
                        "none"
                      ]
                    }
                  },
                  "required": [
                    "nodes"
                  ],
                  "additionalProperties": false
                }
              },
              "required": [
                "action",
                "arguments"
              ],
              "additionalProperties": false
            },
            {
              "type": "object",
              "properties": {
                "action": {
                  "type": "string",
                  "const": "import"
                },
                "arguments": {
                  "type": "object",
                  "properties": {
                    "mode": {
                      "default": "nodes",
                      "type": "string",
                      "enum": [
                        "nodes",
                        "references"
                      ]
                    },
                    "files": {
                      "minItems": 1,
                      "maxItems": 20,
                      "type": "array",
                      "items": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 4096
                      }
                    },
                    "nodeId": {
                      "anyOf": [
                        {
                          "type": "string",
                          "minLength": 1,
                          "maxLength": 160
                        },
                        {
                          "type": "object",
                          "properties": {
                            "taskId": {
                              "type": "string",
                              "format": "uuid",
                              "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
                            },
                            "index": {
                              "type": "integer",
                              "minimum": 0,
                              "maximum": 19
                            }
                          },
                          "required": [
                            "taskId",
                            "index"
                          ],
                          "additionalProperties": false
                        }
                      ]
                    },
                    "layout": {
                      "type": "string",
                      "enum": [
                        "grid",
                        "smart",
                        "none"
                      ]
                    }
                  },
                  "required": [
                    "files"
                  ],
                  "additionalProperties": false
                }
              },
              "required": [
                "action",
                "arguments"
              ],
              "additionalProperties": false
            },
            {
              "type": "object",
              "properties": {
                "action": {
                  "type": "string",
                  "const": "update"
                },
                "arguments": {
                  "type": "object",
                  "properties": {
                    "updates": {
                      "minItems": 1,
                      "maxItems": 20,
                      "type": "array",
                      "items": {
                        "type": "object",
                        "properties": {
                          "nodeId": {
                            "anyOf": [
                              {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 160
                              },
                              {
                                "type": "object",
                                "properties": {
                                  "taskId": {
                                    "type": "string",
                                    "format": "uuid",
                                    "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
                                  },
                                  "index": {
                                    "type": "integer",
                                    "minimum": 0,
                                    "maximum": 19
                                  }
                                },
                                "required": [
                                  "taskId",
                                  "index"
                                ],
                                "additionalProperties": false
                              }
                            ]
                          },
                          "expected": {
                            "type": "object",
                            "properties": {
                              "prompt": {
                                "type": "string",
                                "maxLength": 20000
                              },
                              "model": {
                                "type": "string",
                                "maxLength": 4000
                              },
                              "ratio": {
                                "type": "string",
                                "maxLength": 2000
                              },
                              "resolution": {
                                "type": "string",
                                "maxLength": 2000
                              },
                              "quantity": {
                                "type": "string",
                                "maxLength": 2000
                              },
                              "referenceMode": {
                                "type": "string",
                                "maxLength": 2000
                              },
                              "duration": {
                                "type": "string",
                                "maxLength": 2000
                              }
                            },
                            "additionalProperties": false
                          },
                          "changes": {
                            "type": "object",
                            "properties": {
                              "prompt": {
                                "type": "string",
                                "maxLength": 20000
                              },
                              "model": {
                                "type": "string",
                                "maxLength": 4000
                              },
                              "ratio": {
                                "type": "string",
                                "maxLength": 2000
                              },
                              "resolution": {
                                "type": "string",
                                "maxLength": 2000
                              },
                              "quantity": {
                                "type": "string",
                                "maxLength": 2000
                              },
                              "referenceMode": {
                                "type": "string",
                                "maxLength": 2000
                              },
                              "duration": {
                                "type": "string",
                                "maxLength": 2000
                              }
                            },
                            "additionalProperties": false
                          }
                        },
                        "required": [
                          "nodeId",
                          "expected",
                          "changes"
                        ],
                        "additionalProperties": false
                      }
                    }
                  },
                  "required": [
                    "updates"
                  ],
                  "additionalProperties": false
                }
              },
              "required": [
                "action",
                "arguments"
              ],
              "additionalProperties": false
            },
            {
              "type": "object",
              "properties": {
                "action": {
                  "type": "string",
                  "const": "layout"
                },
                "arguments": {
                  "type": "object",
                  "properties": {
                    "nodeIds": {
                      "minItems": 2,
                      "maxItems": 100,
                      "type": "array",
                      "items": {
                        "anyOf": [
                          {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160
                          },
                          {
                            "type": "object",
                            "properties": {
                              "taskId": {
                                "type": "string",
                                "format": "uuid",
                                "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
                              },
                              "index": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 19
                              }
                            },
                            "required": [
                              "taskId",
                              "index"
                            ],
                            "additionalProperties": false
                          }
                        ]
                      }
                    },
                    "layout": {
                      "type": "string",
                      "enum": [
                        "grid",
                        "smart"
                      ]
                    }
                  },
                  "required": [
                    "nodeIds",
                    "layout"
                  ],
                  "additionalProperties": false
                }
              },
              "required": [
                "action",
                "arguments"
              ],
              "additionalProperties": false
            },
            {
              "type": "object",
              "properties": {
                "action": {
                  "type": "string",
                  "const": "connect"
                },
                "arguments": {
                  "type": "object",
                  "properties": {
                    "sourceNodeId": {
                      "anyOf": [
                        {
                          "type": "string",
                          "minLength": 1,
                          "maxLength": 160
                        },
                        {
                          "type": "object",
                          "properties": {
                            "taskId": {
                              "type": "string",
                              "format": "uuid",
                              "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
                            },
                            "index": {
                              "type": "integer",
                              "minimum": 0,
                              "maximum": 19
                            }
                          },
                          "required": [
                            "taskId",
                            "index"
                          ],
                          "additionalProperties": false
                        }
                      ]
                    },
                    "expectedSourceUrl": {
                      "type": "string",
                      "maxLength": 8192,
                      "format": "uri"
                    },
                    "prompt": {
                      "type": "string",
                      "maxLength": 20000
                    },
                    "referenceMode": {
                      "default": "首尾帧",
                      "type": "string",
                      "enum": [
                        "首尾帧",
                        "全能参考"
                      ]
                    }
                  },
                  "required": [
                    "sourceNodeId",
                    "expectedSourceUrl"
                  ],
                  "additionalProperties": false
                }
              },
              "required": [
                "action",
                "arguments"
              ],
              "additionalProperties": false
            }
          ]
        }
      },
      "required": [
        "teamId",
        "workspaceId",
        "taskId",
        "expectedVersion",
        "op",
        "plan"
      ],
      "additionalProperties": false
    },
    {
      "type": "object",
      "properties": {
        "teamId": {
          "type": "string",
          "format": "uuid",
          "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
        },
        "workspaceId": {
          "type": "string",
          "format": "uuid",
          "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
        },
        "taskId": {
          "type": "string",
          "format": "uuid",
          "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
        },
        "expectedVersion": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "op": {
          "type": "string",
          "const": "cancel"
        }
      },
      "required": [
        "teamId",
        "workspaceId",
        "taskId",
        "expectedVersion",
        "op"
      ],
      "additionalProperties": false
    },
    {
      "type": "object",
      "properties": {
        "teamId": {
          "type": "string",
          "format": "uuid",
          "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
        },
        "workspaceId": {
          "type": "string",
          "format": "uuid",
          "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
        },
        "taskId": {
          "type": "string",
          "format": "uuid",
          "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
        },
        "expectedVersion": {
          "type": "integer",
          "minimum": 0,
          "maximum": 9007199254740991
        },
        "op": {
          "type": "string",
          "const": "acknowledge"
        },
        "acknowledgeStopped": {
          "type": "boolean",
          "const": true
        }
      },
      "required": [
        "teamId",
        "workspaceId",
        "taskId",
        "expectedVersion",
        "op",
        "acknowledgeStopped"
      ],
      "additionalProperties": false
    }
  ]
}
```

调用示例：

```json
{
  "name": "media_collab_task",
  "arguments": {
    "op": "create",
    "teamId": "22222222-2222-4222-8222-222222222222",
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "role": "prompt",
    "title": "示例：准备视频草稿",
    "expectedVersion": 0,
    "dependsOn": []
  }
}
```

## media_collab_execute_next

仅团队创建者且原画布认领者顺序执行下个就绪任务，须唯一requestId及明确核对当前画布的expectedRevision。原样校验版本，不自动接受用户后续改动。重复请求返回原收据；部分失败/未知冻结，不自动重试，不是事务。

```json
{
  "type": "object",
  "properties": {
    "teamId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991
    },
    "requestId": {
      "type": "string",
      "minLength": 1,
      "maxLength": 120
    },
    "expectedRevision": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
      "description": "执行者明确核对当前画布快照后批准的revision；原样传给页面，不能自动替换"
    }
  },
  "required": [
    "teamId",
    "workspaceId",
    "tabId",
    "requestId",
    "expectedRevision"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_collab_execute_next",
  "arguments": {
    "teamId": "22222222-2222-4222-8222-222222222222",
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "requestId": "33333333-3333-4333-8333-333333333333",
    "expectedRevision": "55555555-5555-4555-8555-555555555555"
  }
}
```

## media_collab_reconcile

对waiting导入的已知节点重新只读核对ready状态。证据不足保持阻塞；unknown不自动恢复或重放，不能清除原workspace未知保护。

```json
{
  "type": "object",
  "properties": {
    "teamId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "taskId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "expectedVersion": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991
    },
    "tabId": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991
    }
  },
  "required": [
    "teamId",
    "workspaceId",
    "taskId",
    "expectedVersion",
    "tabId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "media_collab_reconcile",
  "arguments": {
    "teamId": "22222222-2222-4222-8222-222222222222",
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "tabId": 7,
    "taskId": "44444444-4444-4444-8444-444444444444",
    "expectedVersion": 4
  }
}
```

## jimeng_cli_status

只读检查官方CLI版本和命令目录；不登录、不访问画布。CLI需独立安装，不能把离线可运行当实站通过。

```json
{
  "type": "object",
  "properties": {},
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "jimeng_cli_status",
  "arguments": {}
}
```

## jimeng_cli_auth

官方CLI认证：status/account只读，login取得官方授权链接，wait使用本会话challengeId等待。设备码仅在内存，不导出Cookie或凭据；不强制重登录。

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "default": "default",
      "type": "string",
      "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    },
    "region": {
      "default": "cn",
      "type": "string",
      "const": "cn"
    },
    "action": {
      "type": "string",
      "enum": [
        "status",
        "account",
        "login",
        "wait"
      ]
    },
    "challengeId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "timeoutSeconds": {
      "default": 5,
      "type": "integer",
      "minimum": 1,
      "maximum": 30
    }
  },
  "required": [
    "action"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "jimeng_cli_auth",
  "arguments": {
    "action": "status"
  }
}
```

## jimeng_cli_catalog

读取当前模型/音色目录；model list必须选type，query按官方find解析规范模型名，不猜参数或音色。

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "default": "default",
      "type": "string",
      "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    },
    "region": {
      "default": "cn",
      "type": "string",
      "const": "cn"
    },
    "kind": {
      "type": "string",
      "enum": [
        "model",
        "voice"
      ]
    },
    "type": {
      "type": "string",
      "enum": [
        "image",
        "video",
        "audio"
      ]
    },
    "query": {
      "type": "string",
      "minLength": 1,
      "maxLength": 500
    },
    "model": {
      "type": "string",
      "minLength": 1,
      "maxLength": 500
    },
    "offset": {
      "default": 0,
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991
    },
    "count": {
      "default": 50,
      "type": "integer",
      "minimum": 1,
      "maximum": 100
    }
  },
  "required": [
    "kind"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "jimeng_cli_catalog",
  "arguments": {
    "kind": "model",
    "type": "video"
  }
}
```

## jimeng_cli_canvases

只读列出当前CLI profile可访问画布，不选择或创建画布；分页保留nextCursor。

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "default": "default",
      "type": "string",
      "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    },
    "region": {
      "default": "cn",
      "type": "string",
      "const": "cn"
    },
    "limit": {
      "default": 20,
      "type": "integer",
      "minimum": 1,
      "maximum": 100
    },
    "cursor": {
      "type": "string",
      "minLength": 1,
      "maxLength": 500
    }
  },
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "jimeng_cli_canvases",
  "arguments": {}
}
```

## jimeng_cli_claim_canvas

核验官方账号/空间及指定项目访问后认领；与网页DOM通道共享同项目互斥，不虚构tabId。不创建画布。

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "default": "default",
      "type": "string",
      "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    },
    "region": {
      "default": "cn",
      "type": "string",
      "const": "cn"
    },
    "projectId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    }
  },
  "required": [
    "projectId",
    "workspaceId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "jimeng_cli_claim_canvas",
  "arguments": {
    "projectId": "22222222-2222-4222-8222-222222222222",
    "workspaceId": "11111111-1111-4111-8111-111111111111"
  }
}
```

## jimeng_cli_nodes

只读查找或读取官方节点。show返回fingerprint用于编辑前比较；不改变选择，不生成。

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "default": "default",
      "type": "string",
      "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    },
    "region": {
      "default": "cn",
      "type": "string",
      "const": "cn"
    },
    "projectId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "action": {
      "type": "string",
      "enum": [
        "find",
        "show"
      ]
    },
    "nodeIds": {
      "minItems": 1,
      "maxItems": 20,
      "type": "array",
      "items": {
        "type": "string",
        "pattern": "^node_[0-9abcdefghjkmnpqrstvwxyz]{10}$"
      }
    },
    "name": {
      "type": "string",
      "minLength": 1,
      "maxLength": 500
    },
    "type": {
      "type": "string",
      "enum": [
        "image",
        "video",
        "audio",
        "text",
        "element",
        "timeline"
      ]
    },
    "status": {
      "type": "string",
      "minLength": 1,
      "maxLength": 500
    },
    "limit": {
      "default": 20,
      "type": "integer",
      "minimum": 1,
      "maximum": 100
    },
    "offset": {
      "default": 0,
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991
    }
  },
  "required": [
    "projectId",
    "action"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "jimeng_cli_nodes",
  "arguments": {
    "projectId": "22222222-2222-4222-8222-222222222222",
    "action": "show",
    "nodeIds": [
      "node_0000000001"
    ]
  }
}
```

## jimeng_cli_save_node

创建/编辑官方typed节点草稿并读回校验字段和有序节点引用，不带run。需稳定nodeId/updateId；编辑要求最新fingerprint。失败/未知保留原ID，不重放。

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "default": "default",
      "type": "string",
      "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    },
    "region": {
      "default": "cn",
      "type": "string",
      "const": "cn"
    },
    "projectId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "action": {
      "type": "string",
      "enum": [
        "create",
        "edit"
      ]
    },
    "nodeId": {
      "type": "string",
      "pattern": "^node_[0-9abcdefghjkmnpqrstvwxyz]{10}$"
    },
    "updateId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "expectedFingerprint": {
      "type": "string",
      "pattern": "^[a-f0-9]{64}$"
    },
    "draft": {
      "type": "object",
      "properties": {
        "type": {
          "type": "string",
          "enum": [
            "image",
            "video",
            "audio",
            "text",
            "element"
          ]
        },
        "title": {
          "type": "string",
          "minLength": 1,
          "maxLength": 500
        },
        "prompt": {
          "type": "string",
          "maxLength": 20000
        },
        "text": {
          "type": "string",
          "maxLength": 20000
        },
        "description": {
          "type": "string",
          "maxLength": 20000
        },
        "mode": {
          "type": "string",
          "enum": [
            "t2i",
            "i2i",
            "t2v",
            "first_last_frame",
            "m2v",
            "tts",
            "music"
          ]
        },
        "model": {
          "type": "string",
          "minLength": 1,
          "maxLength": 500
        },
        "ratio": {
          "type": "string",
          "minLength": 1,
          "maxLength": 500
        },
        "resolution": {
          "type": "string",
          "minLength": 1,
          "maxLength": 500
        },
        "duration": {
          "type": "number",
          "exclusiveMinimum": 0
        },
        "count": {
          "type": "integer",
          "exclusiveMinimum": 0,
          "maximum": 16
        },
        "references": {
          "maxItems": 40,
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^node_[0-9abcdefghjkmnpqrstvwxyz]{10}$"
          }
        },
        "mainNodeId": {
          "type": "string",
          "pattern": "^node_[0-9abcdefghjkmnpqrstvwxyz]{10}$"
        },
        "voiceNodeId": {
          "type": "string",
          "pattern": "^node_[0-9abcdefghjkmnpqrstvwxyz]{10}$"
        },
        "auxiliaryNodeIds": {
          "maxItems": 40,
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^node_[0-9abcdefghjkmnpqrstvwxyz]{10}$"
          }
        },
        "voiceName": {
          "type": "string",
          "minLength": 1,
          "maxLength": 500
        },
        "scenario": {
          "type": "string",
          "enum": [
            "text-reading",
            "omni-dubbing"
          ]
        },
        "x": {
          "type": "number"
        },
        "y": {
          "type": "number"
        }
      },
      "required": [
        "type"
      ],
      "additionalProperties": false
    }
  },
  "required": [
    "projectId",
    "workspaceId",
    "action",
    "nodeId",
    "updateId",
    "draft"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "jimeng_cli_save_node",
  "arguments": {
    "projectId": "22222222-2222-4222-8222-222222222222",
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "action": "create",
    "nodeId": "node_0000000001",
    "updateId": "33333333-3333-4333-8333-333333333333",
    "draft": {
      "type": "video",
      "mode": "m2v",
      "prompt": "示例完整视频提示词。实际执行逐字读取已审核Excel。",
      "references": [
        "node_0000000002",
        "node_0000000003",
        "node_0000000004"
      ]
    }
  }
}
```

## jimeng_cli_import_media

上传单个本地图片/视频/音频为项目资源并创建独立可见节点，不是原生资产库入库。需稳定resourceId/nodeId/updateId；回读不代表媒体处理已完成。

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "default": "default",
      "type": "string",
      "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    },
    "region": {
      "default": "cn",
      "type": "string",
      "const": "cn"
    },
    "projectId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "file": {
      "type": "string"
    },
    "type": {
      "type": "string",
      "enum": [
        "image",
        "video",
        "audio"
      ]
    },
    "resourceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "nodeId": {
      "type": "string",
      "pattern": "^node_[0-9abcdefghjkmnpqrstvwxyz]{10}$"
    },
    "updateId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "title": {
      "type": "string",
      "minLength": 1,
      "maxLength": 500
    }
  },
  "required": [
    "projectId",
    "workspaceId",
    "file",
    "type",
    "resourceId",
    "nodeId",
    "updateId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "jimeng_cli_import_media",
  "arguments": {
    "projectId": "22222222-2222-4222-8222-222222222222",
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "file": "C:/素材/人物.png",
    "type": "image",
    "resourceId": "33333333-3333-4333-8333-333333333333",
    "nodeId": "node_0000000001",
    "updateId": "33333333-3333-4333-8333-333333333333"
  }
}
```

## jimeng_cli_quote

只读取得已保存节点的完整积分报价及draftVersion，不批准、不生成；积分与人民币换算由实际账户证据另核。

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "default": "default",
      "type": "string",
      "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    },
    "region": {
      "default": "cn",
      "type": "string",
      "const": "cn"
    },
    "projectId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "nodeIds": {
      "minItems": 1,
      "maxItems": 20,
      "type": "array",
      "items": {
        "type": "string",
        "pattern": "^node_[0-9abcdefghjkmnpqrstvwxyz]{10}$"
      }
    }
  },
  "required": [
    "projectId",
    "nodeIds"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "jimeng_cli_quote",
  "arguments": {
    "projectId": "22222222-2222-4222-8222-222222222222",
    "nodeIds": [
      "node_0000000001"
    ]
  }
}
```

## jimeng_cli_run

按既有用户授权运行一个已保存节点，可能消耗积分。要求期望draftVersion、明确积分上限和唯一submitId；先重新报价，内部使用批准token，持久预留身份防重复。返回受理不等于完成。

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "default": "default",
      "type": "string",
      "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    },
    "region": {
      "default": "cn",
      "type": "string",
      "const": "cn"
    },
    "projectId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "workspaceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "nodeId": {
      "type": "string",
      "pattern": "^node_[0-9abcdefghjkmnpqrstvwxyz]{10}$"
    },
    "submitId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "expectedDraftVersion": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991
    },
    "creditCeiling": {
      "type": "integer",
      "minimum": 0,
      "maximum": 9007199254740991
    }
  },
  "required": [
    "projectId",
    "workspaceId",
    "nodeId",
    "submitId",
    "expectedDraftVersion",
    "creditCeiling"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "jimeng_cli_run",
  "arguments": {
    "projectId": "22222222-2222-4222-8222-222222222222",
    "workspaceId": "11111111-1111-4111-8111-111111111111",
    "nodeId": "node_0000000001",
    "submitId": "33333333-3333-4333-8333-333333333333",
    "expectedDraftVersion": 7,
    "creditCeiling": 110
  }
}
```

## jimeng_cli_operation

只读查询原submitId对应的真实操作与资源，不重新提交；absent/unknown均不能作为安全重提证明。

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "default": "default",
      "type": "string",
      "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    },
    "region": {
      "default": "cn",
      "type": "string",
      "const": "cn"
    },
    "projectId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "submitId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    }
  },
  "required": [
    "projectId",
    "submitId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "jimeng_cli_operation",
  "arguments": {
    "projectId": "22222222-2222-4222-8222-222222222222",
    "submitId": "33333333-3333-4333-8333-333333333333"
  }
}
```

## jimeng_cli_resource

只读查询指定资源状态和元信息，不下载、不运行。积分快照不代表最终净扣费。

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "default": "default",
      "type": "string",
      "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    },
    "region": {
      "default": "cn",
      "type": "string",
      "const": "cn"
    },
    "projectId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    },
    "resourceId": {
      "type": "string",
      "format": "uuid",
      "pattern": "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
    }
  },
  "required": [
    "projectId",
    "resourceId"
  ],
  "additionalProperties": false
}
```

调用示例：

```json
{
  "name": "jimeng_cli_resource",
  "arguments": {
    "projectId": "22222222-2222-4222-8222-222222222222",
    "resourceId": "33333333-3333-4333-8333-333333333333"
  }
}
```
