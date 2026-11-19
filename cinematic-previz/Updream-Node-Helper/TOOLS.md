# Updream MCP 接口列表 · 0.3.1

由 server/tools.cjs 自动生成。Codex 通过 MCP tools/list 获得同一套接口。

共 30 个接口：20 个直接接口、5 个效率接口和 5 个多工作区协作接口。需写入的工作先 open_workspace → claim_canvas；所有写入携带 workspaceId。不同项目并行，同项目跨标签互斥；结束后 release_canvas 或 close_workspace。只读检查无需新建工作区。所有节点接口必须指定真实 tabId。

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
| updream_list_tools | 是 | 列出指定 updream 标签页当前实际注册的原生工具及完整参数格式。 |
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
| updream_find_nodes | 是 | 跨页搜索节点名称、ID、提示词摘要，支持类型和状态筛选。返回 nextOffset 继续扫描；达到 maxScan 时明确标记。不是全文搜索或原子快照。 |
| updream_call_native_tool | 否 | 按实时目录调用已注册的 canvas/gen 工具，先验证网页 JSON Schema，再保留网站业务校验与确认。写入仍要求工作区认领。 |
| updream_batch | 否 | 最多 20 步本工作区画布顺序调用。先验证全部实时 JSON Schema，业务校验仍在执行时进行。遇错、拒绝或取消停止并返回 completed/stoppedAt。不是事务，不回滚或自动重试。 |
| updream_update_node_checked | 否 | 在已认领画布修改名称/完整提示词前比较 expected，写后读回核对。每个 changes 字段都需对应 expected；提示词必须来自完整 editDraft.prompt。仍非网站原子条件更新。 |
| updream_diagnose | 是 | 诊断本机、浏览器和可选标签页的版本、启用状态及原生工具注册情况，不返回令牌。 |

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
    "projectId",
    "acknowledgeUnknownOutcome"
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
    "workspaceId",
    "recoverAbandoned",
    "acknowledgeUnknownOutcome"
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

列出指定 updream 标签页当前实际注册的原生工具及完整参数格式。

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
    "tabId",
    "query",
    "offset",
    "limit",
    "maxScan"
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

按实时目录调用已注册的 canvas/gen 工具，先验证网页 JSON Schema，再保留网站业务校验与确认。写入仍要求工作区认领。

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
      "pattern": "^(canvas|gen)[._][A-Za-z][A-Za-z0-9]*$",
      "description": "来自实时工具目录的 canvas/gen 名称"
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
    "tool",
    "arguments"
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
            "pattern": "^(canvas|gen)[._][A-Za-z][A-Za-z0-9]*$",
            "description": "来自实时工具目录的 canvas/gen 名称"
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
          "tool",
          "arguments"
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
